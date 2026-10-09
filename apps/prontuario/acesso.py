"""Camada 4 da autorizacao: autoria, liberacao de leitura e consentimento do paciente (docs/04, secao 3)."""

from django.http import Http404

from apps.contas.models import Perfil
from apps.core.permissoes import consultorio_de_demonstracao
from apps.pacientes.models import ParticipanteGrupo
from apps.pacientes.servico import profissional_da_requisicao

from .models import ConsentimentoPaciente, LiberacaoLeitura, Prontuario

DONO = "dono"
LIBERADO = "liberado"


def pacientes_do_prontuario(prontuario):
    if prontuario.paciente_id:
        return [prontuario.paciente]
    return [p.paciente for p in ParticipanteGrupo.objects.filter(grupo=prontuario.grupo).select_related("paciente")]


def consentimento_vigente(prontuario, profissional_destino) -> bool:
    """Todos os pacientes do prontuario (um, ou cada participante do conjunto) autorizaram o profissional."""
    pacientes = pacientes_do_prontuario(prontuario)
    if not pacientes:
        return False
    return all(
        ConsentimentoPaciente.objects.filter(
            paciente=paciente,
            profissional_origem_id=prontuario.profissional_id,
            profissional_destino=profissional_destino,
            aceito_em__isnull=False,
            revogado_em__isnull=True,
        ).exists()
        for paciente in pacientes
    )


def papel(request, prontuario):
    """DONO, LIBERADO ou None."""
    profissional = profissional_da_requisicao(request)
    if profissional is not None and prontuario.profissional_id == profissional.pk:
        return DONO
    if consultorio_de_demonstracao(request) and getattr(request.vinculo, "perfil", None) == Perfil.ADMIN:
        return LIBERADO  # demonstracao: o administrador le (nunca escreve) todos os prontuarios
    liberado = LiberacaoLeitura.objects.filter(
        prontuario=prontuario, usuario=request.user, revogado_em__isnull=True
    ).exists()
    if not liberado:
        return None
    if request.vinculo.perfil == Perfil.PROFISSIONAL:  # outro profissional: precisa do aceite do paciente
        return LIBERADO if profissional and consentimento_vigente(prontuario, profissional) else None
    return LIBERADO


def exigir_leitura(request, prontuario):
    """Retorna o papel ou responde 404 (nao revela que o prontuario existe)."""
    p = papel(request, prontuario) if prontuario.consultorio_id == request.consultorio.pk else None
    if p is None:
        raise Http404
    return p


def exigir_dono(request, prontuario):
    if exigir_leitura(request, prontuario) != DONO:
        raise Http404
    return DONO


def prontuarios_acessiveis(request):
    """Os do proprio profissional mais os que foram liberados a este usuario (consentimento checado na abertura)."""
    base = Prontuario.objects.filter(consultorio=request.consultorio, excluido_em__isnull=True).select_related(
        "paciente", "grupo", "profissional__usuario"
    )
    profissional = profissional_da_requisicao(request)
    proprios = base.filter(profissional=profissional) if profissional else base.none()
    if consultorio_de_demonstracao(request) and getattr(request.vinculo, "perfil", None) == Perfil.ADMIN:
        return list(proprios), [p for p in base if p not in proprios]
    liberados_ids = LiberacaoLeitura.objects.filter(usuario=request.user, revogado_em__isnull=True).values("prontuario_id")
    liberados = [p for p in base.filter(pk__in=liberados_ids) if papel(request, p) == LIBERADO]
    return list(proprios), liberados
