from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil, Usuario
from apps.core.permissoes import perfil_requerido
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio, PagamentoPlataforma

from . import servico
from .forms import AutorizarSuporteForm, NovoConsultorioForm, PagamentoForm, PlanoForm, StatusForm
from .models import AcessoSuporte
from .servico import ErroOperador


def operador_requerido(view):
    """Equipe da plataforma: usuario `is_staff` com 2FA ativo. Nao e um perfil de consultorio."""

    @wraps(view)
    @login_required
    def envolvida(request, *args, **kwargs):
        if not request.user.is_staff:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if not request.user.segundo_fator_ativo:
            messages.warning(request, "A equipe da plataforma precisa da verificação em duas etapas.")
            return redirect("contas:configurar_2fa")
        return view(request, *args, **kwargs)

    return never_cache(envolvida)


@operador_requerido
def painel(request):
    linhas = []
    for consultorio in Consultorio.objects.select_related("plano").order_by("nome"):
        linhas.append({"c": consultorio, **servico.metadados(consultorio)})
    return render(request, "operador/painel.html", {"linhas": linhas})


@operador_requerido
def novo_consultorio(request):
    form = NovoConsultorioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            consultorio, admin = servico.criar_consultorio_com_admin(
                nome=d["nome"], slug=d["slug"], documento=d["documento"], plano=d["plano"], admin_nome=d["admin_nome"],
                admin_email=d["admin_email"], admin_senha=d["admin_senha"],
            )
        except (ErroOperador, ValidationError) as erro:
            form.add_error(None, "; ".join(getattr(erro, "messages", [str(erro)])))
        else:
            messages.success(request, "Consultório criado. Informe a senha inicial ao administrador por um canal seguro.")
            return redirect("operador:consultorio", pk=consultorio.pk)
    return render(request, "operador/form.html", {"form": form, "titulo": "Novo consultório"})


@operador_requerido
def consultorio(request, pk):
    obj = get_object_or_404(Consultorio.objects.select_related("plano"), pk=pk)
    with contexto(consultorio_id=obj.pk):
        acessos = list(AcessoSuporte.objects.filter(consultorio=obj, operador=request.user)[:10])
        vigente = servico.acesso_vigente(request.user, obj.pk)
    return render(request, "operador/consultorio.html", {
        "c": obj, "meta": servico.metadados(obj), "pagamentos": obj.pagamentos.order_by("-competencia"),
        "plano_form": PlanoForm(initial={"plano": obj.plano}), "status_form": StatusForm(initial={"status": obj.status}),
        "pagamento_form": PagamentoForm(), "acessos": acessos, "suporte_vigente": vigente,
    })


@operador_requerido
@require_POST
def alterar(request, pk):
    obj = get_object_or_404(Consultorio, pk=pk)
    if request.POST.get("acao") == "plano":
        form = PlanoForm(request.POST)
        if form.is_valid():
            servico.mudar_plano(obj, form.cleaned_data["plano"])
            messages.success(request, "Plano atualizado.")
    elif request.POST.get("acao") == "status":
        form = StatusForm(request.POST)
        if form.is_valid():
            obj.status = form.cleaned_data["status"]
            obj.encerrado_em = timezone.now() if obj.status == Consultorio.Status.ENCERRADO else None
            obj.save(update_fields=["status", "encerrado_em", "atualizado_em"])
            messages.success(request, "Situação atualizada.")
    elif request.POST.get("acao") == "pagamento":
        form = PagamentoForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                servico.criar_pagamento(obj, competencia=d["competencia"], valor=d["valor"], vencimento=d["vencimento"])
                messages.success(request, "Cobrança registrada.")
            except ErroOperador as erro:
                messages.error(request, str(erro))
        else:
            messages.error(request, "Confira os campos da cobrança.")
    return redirect("operador:consultorio", pk=pk)


@operador_requerido
@require_POST
def pagar(request, pk, pagamento_pk):
    pagamento = get_object_or_404(PagamentoPlataforma, pk=pagamento_pk, consultorio_id=pk)
    try:
        servico.marcar_pago(pagamento)
        messages.success(request, "Pagamento registrado.")
    except ErroOperador as erro:
        messages.error(request, str(erro))
    return redirect("operador:consultorio", pk=pk)


@operador_requerido
@require_POST
def suporte_entrar(request, pk):
    obj = get_object_or_404(Consultorio, pk=pk)
    with contexto(consultorio_id=obj.pk, usuario_id=request.user.pk):
        acesso = servico.acesso_vigente(request.user, obj.pk)
        if acesso is None:
            messages.error(request, "O cliente ainda não autorizou o suporte, ou a autorização venceu.")
            return redirect("operador:consultorio", pk=pk)
        request.consultorio = obj
        auditoria.registrar(request, "suporte_entrou", "acesso_suporte", acesso.pk, consultorio_id=obj.pk)
    request.session["suporte_acesso_id"], request.session["suporte_consultorio_id"] = str(acesso.pk), str(obj.pk)
    return redirect("painel")


@login_required
@require_POST
def suporte_sair(request):
    request.session.pop("suporte_acesso_id", None)
    request.session.pop("suporte_consultorio_id", None)
    return redirect("operador:painel")


# --------------------------------------------------------------------------- lado do cliente: autorizar e ver o suporte


@perfil_requerido(Perfil.ADMIN)
def suporte_cliente(request):
    form = AutorizarSuporteForm(request.POST or None)
    if request.method == "POST" and not request.session.get("suporte_acesso_id") and form.is_valid():
        d = form.cleaned_data
        operador = Usuario.objects.filter(email__iexact=d["operador_email"], is_staff=True, is_active=True).first()
        try:
            if operador is None:
                raise ErroOperador("Não encontramos esse atendente. Confira o e-mail informado pelo MeuPSIQ.")
            servico.autorizar_suporte(request, operador, horas=d["horas"], motivo=d["motivo"])
        except ErroOperador as erro:
            form.add_error("operador_email", str(erro))
        else:
            messages.success(request, "Acesso de suporte autorizado. Ele é somente leitura e nunca abre prontuários.")
            return redirect("operador:suporte_cliente")
    acessos = AcessoSuporte.objects.filter(consultorio=request.consultorio).select_related("operador")[:30]
    return render(request, "operador/suporte_cliente.html", {"form": form, "acessos": acessos})


@perfil_requerido(Perfil.ADMIN)
@require_POST
def suporte_revogar(request, pk):
    acesso = get_object_or_404(AcessoSuporte, pk=pk, consultorio=request.consultorio, revogado_em__isnull=True)
    servico.revogar_suporte(request, acesso)
    messages.success(request, "Acesso de suporte encerrado.")
    return redirect("operador:suporte_cliente")
