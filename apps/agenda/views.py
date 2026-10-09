from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.core import signing
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.core.permissoes import PERFIS_INTERNOS, perfil_requerido
from apps.core.tenancy import contexto
from apps.pacientes.models import GrupoAtendimento, Paciente
from apps.pacientes.servico import pacientes_visiveis, profissional_da_requisicao

from . import avisos, servico
from .forms import ConsultaForm, RemarcarForm, SolicitacaoForm
from .models import Consulta, SolicitacaoHorario
from .servico import ErroAgenda

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _consultas_visiveis(request):
    consultas = Consulta.objects.filter(consultorio=request.consultorio).select_related(
        "paciente", "grupo", "profissional__usuario"
    )
    profissional = profissional_da_requisicao(request)
    return consultas.filter(profissional=profissional) if profissional else consultas


def _hoje(request):
    return timezone.localtime(timezone.now(), ZoneInfo(request.consultorio.fuso)).date()


@perfil_requerido(*PERFIS_INTERNOS)
def semana(request):
    profissionais = servico.profissionais_do_consultorio(request.consultorio)
    proprio = profissional_da_requisicao(request)
    try:
        referencia = date.fromisoformat(request.GET.get("data", ""))
    except ValueError:
        referencia = _hoje(request)
    segunda = referencia - timedelta(days=referencia.weekday())

    escolhido = proprio
    if proprio is None:
        escolhido = profissionais.filter(pk=request.GET.get("profissional") or None).first() or profissionais.first()

    fuso = ZoneInfo(request.consultorio.fuso)
    inicio = datetime.combine(segunda, datetime.min.time(), tzinfo=fuso)
    consultas = list(
        _consultas_visiveis(request)
        .filter(profissional=escolhido, inicio__gte=inicio, inicio__lt=inicio + timedelta(days=7))
        .exclude(status=Consulta.Status.CANCELADA)
    ) if escolhido else []
    dias = []
    for i in range(7):
        dia = segunda + timedelta(days=i)
        dias.append({
            "data": dia,
            "nome": DIAS[i],
            "hoje": dia == _hoje(request),
            "consultas": [c for c in consultas if timezone.localtime(c.inicio, fuso).date() == dia],
        })
    return render(request, "agenda/semana.html", {
        "dias": dias, "profissionais": profissionais, "escolhido": escolhido, "pode_escolher": proprio is None,
        "anterior": segunda - timedelta(days=7), "proxima": segunda + timedelta(days=7), "segunda": segunda,
    })


@perfil_requerido(*PERFIS_INTERNOS)
def nova_consulta(request):
    profissionais = servico.profissionais_do_consultorio(request.consultorio)
    proprio = profissional_da_requisicao(request)
    pacientes = pacientes_visiveis(request).filter(ativo=True)
    grupos = GrupoAtendimento.objects.filter(consultorio=request.consultorio, ativo=True)
    inicial = {"tipo": "presencial", "repetir": "0"}
    if request.GET.get("paciente"):
        inicial["paciente"] = request.GET["paciente"]
    form = ConsultaForm(
        request.POST or None, initial=inicial, profissionais=profissionais, pacientes=pacientes, grupos=grupos,
        profissional_fixo=proprio,
    )
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            criadas = servico.agendar(
                request, profissional=d["profissional"], inicio=d["inicio"], duracao_minutos=d["duracao_minutos"],
                tipo=d["tipo"], paciente=d["paciente"], grupo=d["grupo"],
                repetir_cada_semanas=int(d["repetir"]), repeticoes=d["repeticoes"] or 1,
            )
        except ErroAgenda as erro:
            form.add_error(None, str(erro))
        else:
            messages.success(request, "Consulta agendada." if len(criadas) == 1 else f"Série agendada: {len(criadas)} sessões.")
            return redirect("agenda:consulta", pk=criadas[0].pk)
    return render(request, "agenda/consulta_form.html", {"form": form})


@perfil_requerido(*PERFIS_INTERNOS)
@require_http_methods(["GET", "POST"])
def consulta(request, pk):
    consulta = get_object_or_404(_consultas_visiveis(request), pk=pk)
    form_remarcar = RemarcarForm()
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "confirmar":
                servico.confirmar(request, consulta)
            elif acao == "cancelar":
                servico.cancelar(request, consulta, Consulta.CanceladoPor.EQUIPE, tardia=bool(request.POST.get("tardia")))
            elif acao == "realizada":
                servico.marcar_realizada(request, consulta)
            elif acao == "faltou":
                servico.marcar_falta(request, consulta)
            elif acao == "remarcar":
                form_remarcar = RemarcarForm(request.POST)
                if not form_remarcar.is_valid():
                    return render(request, "agenda/consulta.html", {"consulta": consulta, "form_remarcar": form_remarcar})
                servico.remarcar(request, consulta, form_remarcar.cleaned_data["inicio"])
            else:
                raise ErroAgenda("Ação desconhecida.")
        except ErroAgenda as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Pronto.")
        return redirect("agenda:consulta", pk=pk)
    return render(request, "agenda/consulta.html", {
        "consulta": consulta, "form_remarcar": form_remarcar, "avisos": consulta.avisos.all()[:10],
        "participantes": avisos.participantes(consulta),
    })


# --------------------------------------------------------------------------- solicitacoes


@perfil_requerido(*PERFIS_INTERNOS)
def solicitacoes(request):
    qs = SolicitacaoHorario.objects.filter(consultorio=request.consultorio).select_related("paciente", "profissional__usuario")
    proprio = profissional_da_requisicao(request)
    if proprio:
        qs = qs.filter(profissional=proprio)
    return render(request, "agenda/solicitacoes.html", {
        "pendentes": qs.filter(status=SolicitacaoHorario.Status.PENDENTE),
        "decididas": qs.exclude(status=SolicitacaoHorario.Status.PENDENTE).order_by("-decidida_em")[:15],
    })


@perfil_requerido(*PERFIS_INTERNOS)
def nova_solicitacao(request):
    proprio = profissional_da_requisicao(request)
    profissionais = servico.profissionais_do_consultorio(request.consultorio)
    if proprio:
        profissionais = profissionais.filter(pk=proprio.pk)
    form = SolicitacaoForm(request.POST or None, profissionais=profissionais, pacientes=pacientes_visiveis(request).filter(ativo=True))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            servico.criar_solicitacao(
                request, paciente=d["paciente"], profissional=d["profissional"], horario=d["horario"], tipo=d["tipo"],
                observacao=d["observacao"],
            )
        except ErroAgenda as erro:
            form.add_error(None, str(erro))
        else:
            messages.success(request, "Solicitação registrada.")
            return redirect("agenda:solicitacoes")
    return render(request, "agenda/solicitacao_form.html", {"form": form})


@perfil_requerido(*PERFIS_INTERNOS)
@require_http_methods(["POST"])
def decidir_solicitacao(request, pk):
    qs = SolicitacaoHorario.objects.filter(consultorio=request.consultorio)
    proprio = profissional_da_requisicao(request)
    if proprio:
        qs = qs.filter(profissional=proprio)
    solicitacao = get_object_or_404(qs, pk=pk)
    try:
        if request.POST.get("decisao") == "aprovar":
            nova = servico.aprovar_solicitacao(request, solicitacao)
            messages.success(request, "Solicitação aprovada e consulta agendada.")
            return redirect("agenda:consulta", pk=nova.pk)
        if request.POST.get("decisao") == "recusar":
            servico.recusar_solicitacao(request, solicitacao)
            messages.success(request, "Solicitação recusada.")
        elif request.POST.get("decisao") == "reabrir":
            servico.reabrir_solicitacao(request, solicitacao)
            messages.success(request, "Solicitação reaberta: volta a aguardar decisão.")
        else:
            raise ErroAgenda("Decisão desconhecida.")
    except ErroAgenda as erro:
        messages.error(request, str(erro))
    return redirect("agenda:solicitacoes")


# --------------------------------------------------------------------------- link publico do aviso


@require_http_methods(["GET", "POST"])
def acao_publica(request, token):
    """Confirmar ou cancelar UMA consulta pelo link do aviso, sem login. O acesso nao expoe nada alem de data e hora."""
    try:
        dados = avisos.ler_token_acao(token)
    except signing.BadSignature:
        return render(request, "agenda/acao_invalida.html", status=404)

    with contexto(consultorio_id=dados["k"]):  # o token assinado define o consultorio; RLS continua valendo
        try:
            consulta = Consulta.objects.select_related("consultorio").get(pk=dados["c"])
        except Consulta.DoesNotExist:
            raise Http404
        pode_agir = consulta.ativa and consulta.inicio > timezone.now()
        resultado = None
        if request.method == "POST" and pode_agir:
            try:
                if request.POST.get("acao") == "confirmar":
                    servico.confirmar(request, consulta)
                    resultado = "Presença confirmada. Obrigado!"
                elif request.POST.get("acao") == "cancelar":
                    servico.cancelar(request, consulta, Consulta.CanceladoPor.PACIENTE)
                    resultado = "Compromisso cancelado."
            except ErroAgenda as erro:
                resultado = str(erro)
            consulta.refresh_from_db()
            pode_agir = consulta.ativa and consulta.inicio > timezone.now()
        local = timezone.localtime(consulta.inicio, ZoneInfo(consulta.consultorio.fuso))
        return render(request, "agenda/acao_publica.html", {
            "consulta": consulta, "quando": local, "pode_agir": pode_agir, "resultado": resultado,
            "ja_confirmada": consulta.status == Consulta.Status.CONFIRMADA,
            "cancelada": consulta.status == Consulta.Status.CANCELADA,
        })
