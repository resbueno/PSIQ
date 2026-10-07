"""Regras da agenda. As views so chamam estas funcoes; mensagens de erro sao para o usuario final."""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil, Profissional, Vinculo
from apps.pacientes.models import Paciente
from apps.pacientes.servico import vincular_profissional

from . import avisos
from .models import AgendaRegra, Aviso, Consulta, SerieRecorrencia, SolicitacaoHorario, TipoAtendimento

LIMITE_REPETICOES = 52


class ErroAgenda(Exception):
    """Regra de negocio violada; a mensagem pode ser exibida ao usuario."""


def profissionais_do_consultorio(consultorio):
    return Profissional.objects.filter(
        ativo=True,
        usuario__vinculos__consultorio=consultorio,
        usuario__vinculos__perfil=Perfil.PROFISSIONAL,
        usuario__vinculos__ativo=True,
    ).select_related("usuario").distinct()


def _validar_profissional(consultorio, profissional):
    if not profissionais_do_consultorio(consultorio).filter(pk=profissional.pk).exists():
        raise ErroAgenda("Este profissional não atende neste consultório.")


def _validar_alvo(consultorio, paciente, grupo):
    if (paciente is None) == (grupo is None):
        raise ErroAgenda("Escolha um paciente ou um grupo de atendimento.")
    alvo = paciente or grupo
    if alvo.consultorio_id != consultorio.pk:
        raise ErroAgenda("Paciente ou grupo não pertence a este consultório.")
    if paciente and paciente.mesclado_em:
        raise ErroAgenda("Este cadastro foi mesclado com outro. Use o cadastro principal.")


def _conflito(profissional, inicio, fim, excluir=None):
    consulta = Consulta.objects.filter(
        profissional=profissional, status__in=Consulta.ATIVAS, inicio__lt=fim, fim__gt=inicio
    )
    if excluir is not None:
        consulta = consulta.exclude(pk=excluir.pk)
    return consulta.first()


def _texto_conflito(existente, consultorio):
    local = timezone.localtime(existente.inicio, ZoneInfo(consultorio.fuso))
    return f"O profissional já tem um compromisso em {local:%d/%m/%Y} às {local:%H:%M}."


def _eh_primeira(paciente, grupo, profissional):
    anteriores = Consulta.objects.filter(profissional=profissional).exclude(status=Consulta.Status.CANCELADA)
    anteriores = anteriores.filter(paciente=paciente) if paciente else anteriores.filter(grupo=grupo)
    return not anteriores.exists()


def exige_aprovacao(paciente, profissional):
    """Primeira consulta sempre passa por aprovacao; retornos so se o paciente estiver marcado assim."""
    return paciente.exige_aprovacao or _eh_primeira(paciente, None, profissional)


def _link_online(tipo):
    if tipo != TipoAtendimento.ONLINE:
        return ""
    return f"{settings.PSIQ_JITSI_URL.rstrip('/')}/psiq-{uuid.uuid4().hex}"


def _criar(consultorio, profissional, inicio, duracao, tipo, paciente, grupo, serie=None):
    fim = inicio + timedelta(minutes=duracao)
    existente = _conflito(profissional, inicio, fim)
    if existente:
        raise ErroAgenda(_texto_conflito(existente, consultorio))
    return Consulta.objects.create(
        consultorio=consultorio,
        profissional=profissional,
        paciente=paciente,
        grupo=grupo,
        inicio=inicio,
        fim=fim,
        tipo=tipo,
        link_online=_link_online(tipo),
        status=Consulta.Status.AGENDADA,
        primeira_consulta=_eh_primeira(paciente, grupo, profissional),
        serie=serie,
    )


def _preparar(request, profissional, inicio, duracao, paciente, grupo):
    consultorio = request.consultorio
    if duracao < 10 or duracao > 480:
        raise ErroAgenda("A duração deve ficar entre 10 minutos e 8 horas.")
    if timezone.is_naive(inicio):
        raise ErroAgenda("Horário inválido.")
    _validar_profissional(consultorio, profissional)
    _validar_alvo(consultorio, paciente, grupo)
    return consultorio


def _ligar_profissional_aos_pacientes(profissional, paciente, grupo):
    for p in ([paciente] if paciente else [x.paciente for x in grupo.participantes.select_related("paciente")]):
        vincular_profissional(p, profissional)


def agendar(request, *, profissional, inicio, duracao_minutos, tipo, paciente=None, grupo=None, repetir_cada_semanas=0, repeticoes=1):
    """Agenda uma consulta, ou uma serie quando `repetir_cada_semanas` > 0. Retorna a lista de consultas criadas."""
    consultorio = _preparar(request, profissional, inicio, duracao_minutos, paciente, grupo)
    if repetir_cada_semanas and not (2 <= repeticoes <= LIMITE_REPETICOES):
        raise ErroAgenda(f"Informe entre 2 e {LIMITE_REPETICOES} repetições.")

    criadas = []
    with transaction.atomic():
        Profissional.objects.select_for_update().get(pk=profissional.pk)  # serializa agendamentos do profissional
        serie = None
        if repetir_cada_semanas:
            serie = SerieRecorrencia.objects.create(
                consultorio=consultorio, profissional=profissional, intervalo_semanas=repetir_cada_semanas, repeticoes=repeticoes
            )
        fuso = ZoneInfo(consultorio.fuso)
        base = inicio.astimezone(fuso)
        for i in range(repeticoes if repetir_cada_semanas else 1):
            ocorrencia = base + timedelta(weeks=i * repetir_cada_semanas)  # mantem o horario de parede
            criadas.append(_criar(consultorio, profissional, ocorrencia, duracao_minutos, tipo, paciente, grupo, serie))
        _ligar_profissional_aos_pacientes(profissional, paciente, grupo)
        auditoria.registrar(
            request, "consulta_agendada", "consulta", criadas[0].pk, ocorrencias=len(criadas), serie=bool(serie)
        )
    avisos.enviar(criadas[0], Aviso.Tipo.CONFIRMACAO)
    return criadas


def cancelar(request, consulta, por, tardia=False):
    """`por`: 'equipe' ou 'paciente'. O paciente so cancela dentro do prazo do consultorio."""
    if not consulta.ativa:
        raise ErroAgenda("Esta consulta não pode mais ser cancelada.")
    agora = timezone.now()
    horas = consulta.consultorio.politica_cancelamento_horas
    dentro_do_prazo_minimo = consulta.inicio - agora < timedelta(hours=horas)
    if por == Consulta.CanceladoPor.PACIENTE and dentro_do_prazo_minimo:
        raise ErroAgenda(
            f"O cancelamento por aqui é possível até {horas} horas antes do horário. Entre em contato com o consultório."
        )
    consulta.status = Consulta.Status.CANCELADA
    consulta.cancelada_em = agora
    consulta.cancelada_por = por
    consulta.falta_tardia = bool(tardia and por == Consulta.CanceladoPor.EQUIPE)
    consulta.save(update_fields=["status", "cancelada_em", "cancelada_por", "falta_tardia", "atualizado_em"])
    auditoria.registrar(
        request, "consulta_cancelada", "consulta", consulta.pk, consultorio_id=consulta.consultorio_id, por=por,
        falta_tardia=consulta.falta_tardia,
    )
    avisos.enviar(consulta, Aviso.Tipo.CANCELAMENTO)


def confirmar(request, consulta):
    if consulta.status != Consulta.Status.AGENDADA:
        raise ErroAgenda("Só é possível confirmar uma consulta agendada.")
    consulta.status = Consulta.Status.CONFIRMADA
    consulta.save(update_fields=["status", "atualizado_em"])
    auditoria.registrar(request, "consulta_confirmada", "consulta", consulta.pk, consultorio_id=consulta.consultorio_id)


def remarcar(request, consulta, novo_inicio):
    if not consulta.ativa:
        raise ErroAgenda("Esta consulta não pode ser remarcada.")
    duracao = consulta.fim - consulta.inicio
    novo_fim = novo_inicio + duracao
    with transaction.atomic():
        Profissional.objects.select_for_update().get(pk=consulta.profissional_id)
        existente = _conflito(consulta.profissional, novo_inicio, novo_fim, excluir=consulta)
        if existente:
            raise ErroAgenda(_texto_conflito(existente, consulta.consultorio))
        consulta.inicio, consulta.fim = novo_inicio, novo_fim
        consulta.status = Consulta.Status.AGENDADA
        consulta.save(update_fields=["inicio", "fim", "status", "atualizado_em"])
        auditoria.registrar(request, "consulta_remarcada", "consulta", consulta.pk)
    avisos.enviar(consulta, Aviso.Tipo.REMARCACAO)


def _participantes(consulta):
    return [consulta.paciente] if consulta.paciente_id else avisos.participantes(consulta)


def marcar_realizada(request, consulta):
    if consulta.status not in (Consulta.Status.AGENDADA, Consulta.Status.CONFIRMADA):
        raise ErroAgenda("Só é possível registrar como realizada uma consulta agendada ou confirmada.")
    if consulta.inicio > timezone.now():
        raise ErroAgenda("A consulta ainda não começou.")
    consulta.status = Consulta.Status.REALIZADA
    consulta.save(update_fields=["status", "atualizado_em"])
    for paciente in _participantes(consulta):
        Paciente.objects.filter(pk=paciente.pk).update(ultimo_atendimento_em=consulta.inicio)
    auditoria.registrar(request, "consulta_realizada", "consulta", consulta.pk)


def marcar_falta(request, consulta):
    if consulta.status not in (Consulta.Status.AGENDADA, Consulta.Status.CONFIRMADA):
        raise ErroAgenda("Só é possível registrar falta de uma consulta agendada ou confirmada.")
    if consulta.inicio > timezone.now():
        raise ErroAgenda("A consulta ainda não começou.")
    consulta.status = Consulta.Status.FALTOU
    consulta.save(update_fields=["status", "atualizado_em"])
    auditoria.registrar(request, "consulta_falta", "consulta", consulta.pk)


# --------------------------------------------------------------------------- solicitacoes


def criar_solicitacao(request, *, paciente, profissional, horario, tipo, observacao=""):
    consultorio = request.consultorio
    _validar_profissional(consultorio, profissional)
    _validar_alvo(consultorio, paciente, None)
    solicitacao = SolicitacaoHorario.objects.create(
        consultorio=consultorio, paciente=paciente, profissional=profissional, horario_desejado=horario, tipo=tipo,
        observacao=observacao,
    )
    auditoria.registrar(request, "solicitacao_criada", "solicitacao", solicitacao.pk)
    return solicitacao


def aprovar_solicitacao(request, solicitacao, duracao_minutos=50):
    if solicitacao.status != SolicitacaoHorario.Status.PENDENTE:
        raise ErroAgenda("Esta solicitação já foi decidida.")
    if solicitacao.horario_desejado < timezone.now():
        raise ErroAgenda("O horário solicitado já passou. Recuse e combine outro horário com o paciente.")
    (consulta,) = agendar(
        request, profissional=solicitacao.profissional, inicio=solicitacao.horario_desejado,
        duracao_minutos=duracao_minutos, tipo=solicitacao.tipo, paciente=solicitacao.paciente,
    )
    solicitacao.status = SolicitacaoHorario.Status.APROVADA
    solicitacao.consulta = consulta
    solicitacao.decidida_por_id = request.user.pk
    solicitacao.decidida_em = timezone.now()
    solicitacao.save()
    auditoria.registrar(request, "solicitacao_aprovada", "solicitacao", solicitacao.pk)
    return consulta


def recusar_solicitacao(request, solicitacao):
    if solicitacao.status != SolicitacaoHorario.Status.PENDENTE:
        raise ErroAgenda("Esta solicitação já foi decidida.")
    solicitacao.status = SolicitacaoHorario.Status.RECUSADA
    solicitacao.decidida_por_id = request.user.pk
    solicitacao.decidida_em = timezone.now()
    solicitacao.save()
    auditoria.registrar(request, "solicitacao_recusada", "solicitacao", solicitacao.pk)


# --------------------------------------------------------------------------- horarios livres


def horarios_livres(consultorio, profissional, dia):
    """Horarios das regras semanais do profissional que ainda nao tem compromisso (datas no fuso do consultorio)."""
    fuso = ZoneInfo(consultorio.fuso)
    agora = timezone.now()
    livres = []
    for regra in AgendaRegra.objects.filter(profissional=profissional, dia_semana=dia.weekday()):
        passo = timedelta(minutes=regra.duracao_minutos)
        atual = datetime.combine(dia, regra.hora_inicio, tzinfo=fuso)
        limite = datetime.combine(dia, regra.hora_fim, tzinfo=fuso)
        while atual + passo <= limite:
            if atual > agora and not _conflito(profissional, atual, atual + passo):
                livres.append(atual)
            atual += passo
    return sorted(livres)
