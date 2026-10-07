import secrets

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil
from apps.core.permissoes import perfil_requerido

from . import sync
from .models import ContaCalendario
from .provedores import PROVEDORES, ErroProvedor

SAL_STATE = "psiq.calendario"


def _redirect_uri(provedor):
    return settings.PSIQ_URL_BASE.rstrip("/") + reverse("calendarios:retorno", args=[provedor])


def _meu_perfil_profissional(request):
    return request.user.profissional


@perfil_requerido(Perfil.PROFISSIONAL)
def lista(request):
    profissional = _meu_perfil_profissional(request)
    contas = {c.provedor: c for c in ContaCalendario.objects.filter(consultorio=request.consultorio, profissional=profissional)}
    opcoes = [{"chave": chave, "rotulo": ContaCalendario.Provedor(chave).label, "configurado": p.configurado(), "conta": contas.get(chave)} for chave, p in PROVEDORES.items()]
    return render(request, "calendarios/lista.html", {"opcoes": opcoes})


@perfil_requerido(Perfil.PROFISSIONAL)
def conectar(request, provedor):
    if provedor not in PROVEDORES:
        raise Http404
    if not PROVEDORES[provedor].configurado():
        messages.error(request, "Esta integração ainda não foi configurada pelo administrador do sistema.")
        return redirect("calendarios:lista")
    nonce = secrets.token_urlsafe(16)
    request.session["calendario_nonce"] = nonce
    state = signing.dumps({"u": str(request.user.pk), "p": provedor, "n": nonce}, salt=SAL_STATE)
    return redirect(PROVEDORES[provedor].url_autorizacao(state, _redirect_uri(provedor)))


@perfil_requerido(Perfil.PROFISSIONAL)
def retorno(request, provedor):
    """Volta do provedor com ?code e ?state. O state assinado amarra a resposta a este usuario e a esta sessao."""
    if provedor not in PROVEDORES:
        raise Http404
    esperado = request.session.pop("calendario_nonce", None)
    try:
        dados = signing.loads(request.GET.get("state", ""), salt=SAL_STATE, max_age=600)
    except signing.BadSignature:
        dados = None
    if not dados or dados["u"] != str(request.user.pk) or dados["p"] != provedor or not esperado or dados["n"] != esperado:
        messages.error(request, "Não foi possível confirmar a conexão. Tente de novo.")
        return redirect("calendarios:lista")
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "A conexão foi cancelada.")
        return redirect("calendarios:lista")
    try:
        tokens = PROVEDORES[provedor].trocar_codigo(request.GET["code"], _redirect_uri(provedor))
    except (ErroProvedor, KeyError):
        messages.error(request, "O provedor recusou a conexão. Tente de novo.")
        return redirect("calendarios:lista")
    conta, _ = ContaCalendario.objects.get_or_create(
        profissional=_meu_perfil_profissional(request), provedor=provedor, defaults={"consultorio": request.consultorio}
    )
    conta.consultorio, conta.email_conta, conta.ativa = request.consultorio, tokens.get("email", "")[:254], True
    conta.guardar_tokens(tokens["acesso"], tokens.get("refresh"), tokens["expira_em"])
    conta.save()
    auditoria.registrar(request, "calendario_conectado", "conta_calendario", conta.pk, provedor=provedor)
    sync.sincronizar(conta)
    messages.success(request, "Calendário conectado.")
    return redirect("calendarios:lista")


def _conta(request, pk):
    return get_object_or_404(ContaCalendario, pk=pk, consultorio=request.consultorio, profissional=_meu_perfil_profissional(request))


@perfil_requerido(Perfil.PROFISSIONAL)
@require_POST
def sincronizar(request, pk):
    conta = _conta(request, pk)
    if sync.sincronizar(conta):
        messages.success(request, "Calendário sincronizado.")
    else:
        messages.error(request, "Não foi possível sincronizar agora. Se continuar, reconecte o calendário.")
    return redirect("calendarios:lista")


@perfil_requerido(Perfil.PROFISSIONAL)
@require_POST
def alternar_nome(request, pk):
    conta = _conta(request, pk)
    conta.mostra_nome_paciente = not conta.mostra_nome_paciente
    conta.save(update_fields=["mostra_nome_paciente", "atualizado_em"])
    auditoria.registrar(request, "calendario_nome_paciente", "conta_calendario", conta.pk, mostra=conta.mostra_nome_paciente)
    sync.sincronizar(conta)
    return redirect("calendarios:lista")


@perfil_requerido(Perfil.PROFISSIONAL)
@require_POST
def desconectar(request, pk):
    conta = _conta(request, pk)
    auditoria.registrar(request, "calendario_desconectado", "conta_calendario", conta.pk, provedor=conta.provedor)
    sync.desconectar(conta)
    messages.success(request, "Calendário desconectado e tokens apagados.")
    return redirect("calendarios:lista")
