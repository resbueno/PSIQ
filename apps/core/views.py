from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.agenda.models import Consulta, SolicitacaoHorario
from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil, Vinculo
from apps.pacientes.servico import pacientes_visiveis, profissional_da_requisicao

DIAS_SEM_CONSULTA = 60


@login_required
def painel(request):
    if request.vinculo is None:
        return redirect("contas:escolher_consultorio")

    fuso = ZoneInfo(request.consultorio.fuso)
    inicio_dia = datetime.combine(timezone.localtime(timezone.now(), fuso).date(), datetime.min.time(), tzinfo=fuso)
    consultas = Consulta.objects.filter(consultorio=request.consultorio).select_related("paciente", "grupo", "profissional__usuario")
    solicitacoes = SolicitacaoHorario.objects.filter(consultorio=request.consultorio, status=SolicitacaoHorario.Status.PENDENTE)
    proprio = profissional_da_requisicao(request)
    if proprio:
        consultas = consultas.filter(profissional=proprio)
        solicitacoes = solicitacoes.filter(profissional=proprio)

    limite = timezone.now() - timedelta(days=DIAS_SEM_CONSULTA)
    contexto = {
        "consultas_hoje": consultas.filter(
            inicio__gte=inicio_dia, inicio__lt=inicio_dia + timedelta(days=1), status__in=Consulta.ATIVAS
        ),
        "solicitacoes_pendentes": solicitacoes.count(),
        "sem_consulta": pacientes_visiveis(request)
        .filter(ativo=True)
        .filter(Q(ultimo_atendimento_em__lt=limite) | Q(ultimo_atendimento_em__isnull=True))
        .count(),
        "dias_sem_consulta": DIAS_SEM_CONSULTA,
    }
    if request.vinculo.perfil == Perfil.ADMIN:
        contexto["usuarios_ativos"] = Vinculo.objects.filter(consultorio=request.consultorio, ativo=True).count()
        contexto["eventos"] = Auditoria.objects.all()[:8]
    return render(request, "core/painel.html", contexto)


def service_worker(request):
    """Service worker na raiz (escopo '/'). Sem cache de dados; usado so para push."""
    resposta = render(request, "pwa/sw.js", content_type="application/javascript")
    resposta["Service-Worker-Allowed"] = "/"
    resposta["Cache-Control"] = "no-cache"
    return resposta


def manifesto(request):
    return render(request, "pwa/manifest.webmanifest", content_type="application/manifest+json")
