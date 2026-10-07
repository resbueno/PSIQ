import json
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.agenda import servico as agenda
from apps.agenda.models import AgendaRegra, Consulta, SolicitacaoHorario, TipoAtendimento
from apps.agenda.servico import ErroAgenda
from apps.auditoria import servico as auditoria
from apps.auditoria.models import Auditoria
from apps.contas.forms import CodigoForm
from apps.contas.models import Usuario
from apps.core.permissoes import PERFIS_INTERNOS, perfil_requerido
from apps.core.tenancy import contexto
from apps.financeiro import servico as financeiro
from apps.financeiro.models import Lancamento, Recibo
from apps.pacientes.models import Paciente
from apps.plataforma.models import Consultorio
from apps.prontuario import servico as prontuario
from apps.prontuario.models import ConsentimentoPaciente, Documento, Prontuario
from apps.prontuario.servico import ErroProntuario

from . import servico
from .models import AssinaturaPush, SolicitacaoLGPD

JANELA_DIAS = 14


class EmailForm(forms.Form):
    email = forms.EmailField(label="Seu e-mail", widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))


class LGPDForm(forms.Form):
    tipo = forms.ChoiceField(label="O que você precisa?", choices=SolicitacaoLGPD.Tipo.choices)
    mensagem = forms.CharField(label="Detalhes (opcional)", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}),
                               help_text="Não escreva informações de saúde aqui.")


def _consultorio_ativo(pk):
    return get_object_or_404(Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO), pk=pk)


def portal_logado(view):
    """Exige sessao do portal; define o consultorio no banco (RLS), o paciente atual e o fuso do consultorio."""

    @wraps(view)
    def envolvida(request, *args, **kwargs):
        dados = request.session.get("portal")
        if not dados:
            return render(request, "portal/sem_sessao.html", status=401)
        consultorio = Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO).filter(pk=dados["c"]).first()
        if consultorio is None:
            request.session.pop("portal", None)
            return render(request, "portal/sem_sessao.html", status=401)
        with contexto(consultorio_id=consultorio.pk), timezone.override(ZoneInfo(consultorio.fuso)):
            pacientes = list(Paciente.objects.filter(consultorio=consultorio, pk__in=dados["p"], ativo=True, mesclado_em__isnull=True).order_by("nome"))
            if not pacientes:
                request.session.pop("portal", None)
                return render(request, "portal/sem_sessao.html", status=401)
            request.consultorio = consultorio
            request.pacientes_portal = pacientes
            request.paciente = next((p for p in pacientes if str(p.pk) == dados.get("atual")), pacientes[0])
            return view(request, *args, **kwargs)

    return never_cache(envolvida)


# --------------------------------------------------------------------------- entrada


@never_cache
def entrar(request, consultorio_id):
    consultorio = _consultorio_ativo(consultorio_id)
    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        with contexto(consultorio_id=consultorio.pk):
            request.consultorio = consultorio
            servico.solicitar_codigo(request, consultorio, email)
        request.session["portal_email_pendente"] = email.strip().lower()
        messages.success(request, "Se o e-mail estiver cadastrado, enviamos um código. Ele vale por poucos minutos.")
        return redirect("portal:codigo", consultorio_id=consultorio.pk)
    return render(request, "portal/entrar.html", {"form": form, "consultorio": consultorio})


@never_cache
def codigo(request, consultorio_id):
    consultorio = _consultorio_ativo(consultorio_id)
    email = request.session.get("portal_email_pendente")
    if not email:
        return redirect("portal:entrar", consultorio_id=consultorio.pk)
    form = CodigoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with contexto(consultorio_id=consultorio.pk):
            request.consultorio = consultorio
            pacientes = servico.validar_codigo(request, consultorio, email, form.codigo_limpo())
        if pacientes:
            request.session.cycle_key()
            request.session.pop("portal_email_pendente", None)
            request.session["portal"] = {"c": str(consultorio.pk), "p": [str(p.pk) for p in pacientes], "atual": str(pacientes[0].pk)}
            return redirect("portal:home")
        form.add_error("codigo", "Código incorreto ou vencido. Peça um novo código.")
    return render(request, "portal/codigo.html", {"form": form, "consultorio": consultorio, "email": email})


@require_POST
def sair(request):
    request.session.pop("portal", None)
    request.session.pop("portal_email_pendente", None)
    return render(request, "portal/sem_sessao.html", {"saiu": True})


@portal_logado
@require_POST
def trocar_paciente(request):
    escolhido = next((p for p in request.pacientes_portal if str(p.pk) == request.POST.get("paciente")), None)
    if escolhido:
        dados = request.session["portal"]
        dados["atual"] = str(escolhido.pk)
        request.session["portal"] = dados
    return redirect("portal:home")


# --------------------------------------------------------------------------- consultas


@portal_logado
def home(request):
    agora = timezone.now()
    consultas = Consulta.objects.filter(paciente=request.paciente).select_related("profissional__usuario")
    return render(request, "portal/home.html", {
        "proximas": consultas.filter(inicio__gte=agora, status__in=Consulta.ATIVAS),
        "pendentes": SolicitacaoHorario.objects.filter(paciente=request.paciente, status=SolicitacaoHorario.Status.PENDENTE),
        "agora": agora, "sala_a_partir_de": agora + timedelta(minutes=15),
        "vapid_public": settings.PSIQ_VAPID_PUBLIC_KEY,
    })


@portal_logado
@require_POST
def consulta_acao(request, pk):
    consulta = get_object_or_404(Consulta.objects.select_related("consultorio"), pk=pk, paciente=request.paciente)
    try:
        if request.POST.get("acao") == "confirmar":
            agenda.confirmar(request, consulta)
            messages.success(request, "Presença confirmada.")
        elif request.POST.get("acao") == "cancelar":
            agenda.cancelar(request, consulta, Consulta.CanceladoPor.PACIENTE)
            messages.success(request, "Compromisso cancelado.")
    except ErroAgenda as erro:
        messages.error(request, str(erro))
    return redirect("portal:home")


def _profissionais_para(request):
    todos = agenda.profissionais_do_consultorio(request.consultorio)
    vinculados = todos.filter(pacientes__paciente=request.paciente)
    return (vinculados if vinculados.exists() else todos), vinculados.exists()


def _slots(request, profissional):
    fuso = ZoneInfo(request.consultorio.fuso)
    hoje = timezone.localtime(timezone.now(), fuso).date()
    dias = []
    for i in range(JANELA_DIAS):
        dia = hoje + timedelta(days=i)
        horarios = agenda.horarios_livres(request.consultorio, profissional, dia)
        if horarios:
            dias.append({"data": dia, "horarios": horarios})
    return dias


@portal_logado
def agendar(request):
    profissionais, ja_tem_vinculo = _profissionais_para(request)
    escolhido = profissionais.filter(pk=request.GET.get("profissional") or request.POST.get("profissional") or None).first() or profissionais.first()

    if request.method == "POST" and escolhido:
        try:
            inicio = datetime.fromisoformat(request.POST.get("inicio", ""))
        except ValueError:
            inicio = None
        livres = set(agenda.horarios_livres(request.consultorio, escolhido, inicio.astimezone(ZoneInfo(request.consultorio.fuso)).date())) if inicio and inicio.tzinfo else set()
        if inicio is None or inicio not in livres:
            messages.error(request, "Esse horário não está mais disponível. Escolha outro.")
            return redirect(f"{request.path}?profissional={escolhido.pk}")
        tipo = request.POST.get("tipo") if request.POST.get("tipo") in TipoAtendimento.values else TipoAtendimento.PRESENCIAL
        regra = AgendaRegra.objects.filter(profissional=escolhido, dia_semana=inicio.astimezone(ZoneInfo(request.consultorio.fuso)).weekday()).first()
        try:
            if agenda.exige_aprovacao(request.paciente, escolhido):
                agenda.criar_solicitacao(request, paciente=request.paciente, profissional=escolhido, horario=inicio, tipo=tipo)
                messages.success(request, "Pedido enviado. O consultório vai confirmar e avisar você.")
            else:
                agenda.agendar(request, profissional=escolhido, inicio=inicio, duracao_minutos=regra.duracao_minutos if regra else 50, tipo=tipo, paciente=request.paciente)
                messages.success(request, "Horário marcado. Enviamos a confirmação por e-mail.")
        except ErroAgenda as erro:
            messages.error(request, str(erro))
            return redirect(f"{request.path}?profissional={escolhido.pk}")
        return redirect("portal:home")

    return render(request, "portal/agendar.html", {
        "profissionais": profissionais, "escolhido": escolhido, "dias": _slots(request, escolhido) if escolhido else [],
        "com_aprovacao": bool(escolhido) and agenda.exige_aprovacao(request.paciente, escolhido), "tipos": TipoAtendimento.choices,
    })


# --------------------------------------------------------------------------- pagamentos e documentos


@portal_logado
def pagamentos(request):
    lancamentos = Lancamento.objects.filter(paciente=request.paciente).exclude(status=Lancamento.Status.CANCELADO).select_related("recibo")
    return render(request, "portal/pagamentos.html", {"lancamentos": lancamentos[:100]})


@portal_logado
def recibo(request, recibo_pk):
    obj = get_object_or_404(Recibo.objects.select_related("lancamento"), pk=recibo_pk, lancamento__paciente=request.paciente)
    pdf = financeiro.baixar_recibo(request, obj)
    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = content_disposition_header(True, f"recibo-{obj.numero:05d}.pdf")
    return resposta


def _documentos_do_paciente(paciente):
    """So o que o profissional liberou, mais declaracoes de comparecimento (sem conteudo clinico)."""
    from django.db.models import Q

    return Documento.objects.filter(paciente=paciente).filter(Q(liberado_ao_paciente=True) | Q(chave__isnull=True))


@portal_logado
def documentos(request):
    return render(request, "portal/documentos.html", {"documentos": _documentos_do_paciente(request.paciente)})


@portal_logado
def documento(request, documento_pk):
    obj = get_object_or_404(_documentos_do_paciente(request.paciente), pk=documento_pk)
    pdf = prontuario.baixar_documento(request, obj)
    auditoria.registrar(request, "documento_baixado_pelo_paciente", "documento", obj.pk, consultorio_id=request.consultorio.pk, paciente=str(request.paciente.pk))
    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = content_disposition_header(True, f"{obj.titulo}.pdf")
    return resposta


# --------------------------------------------------------------------------- privacidade


@portal_logado
def privacidade(request):
    ids = [str(p) for p in Prontuario.objects.filter(paciente=request.paciente).values_list("pk", flat=True)]
    eventos = list(Auditoria.objects.filter(objeto="prontuario", objeto_id__in=ids, acao="prontuario_aberto").order_by("-criado_em")[:50])
    nomes = dict(Usuario.objects.filter(pk__in=[e.usuario_id for e in eventos if e.usuario_id]).values_list("pk", "nome"))
    acessos = [{"quando": e.criado_em, "quem": nomes.get(e.usuario_id, "Profissional do consultório")} for e in eventos]
    form = LGPDForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        servico.abrir_solicitacao_lgpd(request, request.paciente, form.cleaned_data["tipo"], form.cleaned_data["mensagem"])
        messages.success(request, "Pedido registrado. O consultório vai responder por e-mail.")
        return redirect("portal:privacidade")
    return render(request, "portal/privacidade.html", {
        "acessos": acessos, "form": form,
        "consentimentos": ConsentimentoPaciente.objects.filter(paciente=request.paciente, aceito_em__isnull=False, revogado_em__isnull=True).select_related("profissional_destino__usuario", "profissional_origem__usuario"),
        "pedidos": SolicitacaoLGPD.objects.filter(paciente=request.paciente)[:10],
    })


@portal_logado
@require_POST
def revogar_consentimento(request, pk):
    consentimento = get_object_or_404(ConsentimentoPaciente, pk=pk, paciente=request.paciente, revogado_em__isnull=True)
    try:
        prontuario.revogar_consentimento(consentimento, request.META.get("REMOTE_ADDR") or None)
        messages.success(request, "Autorização revogada.")
    except ErroProntuario as erro:
        messages.error(request, str(erro))
    return redirect("portal:privacidade")


# --------------------------------------------------------------------------- equipe: pedidos do titular


@perfil_requerido(*PERFIS_INTERNOS)
def lgpd_lista(request):
    qs = SolicitacaoLGPD.objects.filter(consultorio=request.consultorio).select_related("paciente")
    return render(request, "portal/lgpd_lista.html", {"abertas": qs.filter(status="aberta"), "atendidas": qs.filter(status="atendida")[:20]})


@perfil_requerido(*PERFIS_INTERNOS)
@require_POST
def lgpd_atender(request, pk):
    solicitacao = get_object_or_404(SolicitacaoLGPD, pk=pk, consultorio=request.consultorio, status="aberta")
    servico.atender_solicitacao_lgpd(request, solicitacao)
    messages.success(request, "Pedido marcado como atendido.")
    return redirect("portal:lgpd_lista")


# --------------------------------------------------------------------------- notificacoes push


def _json_do_corpo(request):
    try:
        dados = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return dados if isinstance(dados, dict) else None


@portal_logado
@require_POST
def push_inscrever(request):
    dados = _json_do_corpo(request) or {}
    chaves = dados.get("keys") or {}
    endpoint, p256dh, auth = dados.get("endpoint", ""), chaves.get("p256dh", ""), chaves.get("auth", "")
    if not (isinstance(endpoint, str) and endpoint.startswith("https://") and len(endpoint) < 2000 and p256dh and auth):
        return JsonResponse({"ok": False}, status=400)
    AssinaturaPush.objects.update_or_create(
        paciente=request.paciente, endpoint=endpoint,
        defaults={"consultorio": request.consultorio, "p256dh": str(p256dh)[:200], "auth": str(auth)[:100], "ativa": True},
    )
    auditoria.registrar(request, "push_inscrito", "paciente", request.paciente.pk, consultorio_id=request.consultorio.pk)
    return JsonResponse({"ok": True})


@portal_logado
@require_POST
def push_cancelar(request):
    dados = _json_do_corpo(request) or {}
    AssinaturaPush.objects.filter(paciente=request.paciente, endpoint=dados.get("endpoint", "")).update(ativa=False)
    return JsonResponse({"ok": True})
