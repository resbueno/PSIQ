"""Sincronizacao em duas vias: consultas vao ao calendario externo (so 'Consulta' e horario); compromissos pessoais
voltam como bloqueios de horario. Falhas do provedor ficam registradas na conta e nunca derrubam o sistema."""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.agenda.models import Consulta

from . import provedores
from .models import ContaCalendario, EventoExterno
from .provedores import ErroProvedor

logger = logging.getLogger(__name__)
MARGEM_RENOVACAO = timedelta(seconds=90)


def provedor_da(conta):
    return provedores.PROVEDORES[conta.provedor]


def _token_valido(conta) -> str:
    if conta.expira_em is None or conta.expira_em - timezone.now() > MARGEM_RENOVACAO:
        return conta.token_acesso
    novos = provedor_da(conta).renovar(conta.token_refresh)
    conta.guardar_tokens(novos["acesso"], novos.get("refresh"), novos["expira_em"])
    conta.save(update_fields=["token_acesso_cifrado", "token_refresh_cifrado", "expira_em", "atualizado_em"])
    return novos["acesso"]


def _titulo(conta, consulta) -> str:
    if conta.mostra_nome_paciente:
        alvo = consulta.paciente.nome if consulta.paciente_id else consulta.grupo.nome
        return f"Consulta - {alvo}"
    return "Consulta"


def _exportar(conta, acesso, agora, janela_fim):
    provedor = provedor_da(conta)
    existentes = {e.consulta_id: e for e in conta.eventos.filter(origem=EventoExterno.Origem.PSIQ)}
    consultas = Consulta.objects.filter(
        consultorio=conta.consultorio, profissional=conta.profissional, status__in=Consulta.ATIVAS,
        inicio__gte=agora - timedelta(days=1), inicio__lt=janela_fim,
    ).select_related("paciente", "grupo")
    ativas = set()
    for consulta in consultas:
        ativas.add(consulta.pk)
        evento = existentes.get(consulta.pk)
        titulo = _titulo(conta, consulta)
        if evento is None:
            id_externo = provedor.criar(acesso, titulo, consulta.inicio, consulta.fim)
            EventoExterno.objects.create(
                consultorio=conta.consultorio, conta=conta, origem=EventoExterno.Origem.PSIQ, consulta=consulta,
                id_externo=id_externo, titulo=titulo, inicio=consulta.inicio, fim=consulta.fim,
            )
        elif (evento.inicio, evento.fim, evento.titulo) != (consulta.inicio, consulta.fim, titulo):
            provedor.atualizar(acesso, evento.id_externo, titulo, consulta.inicio, consulta.fim)
            evento.inicio, evento.fim, evento.titulo = consulta.inicio, consulta.fim, titulo
            evento.save(update_fields=["inicio", "fim", "titulo", "atualizado_em"])
    for consulta_id, evento in existentes.items():  # consulta cancelada, concluida ou fora da janela
        if consulta_id not in ativas:
            provedor.apagar(acesso, evento.id_externo)
            evento.delete()


def _importar(conta, acesso, agora, janela_fim):
    provedor = provedor_da(conta)
    nossos = set(conta.eventos.filter(origem=EventoExterno.Origem.PSIQ).values_list("id_externo", flat=True))
    remotos = [e for e in provedor.listar(acesso, agora, janela_fim) if e["id"] not in nossos]
    conta.eventos.filter(origem=EventoExterno.Origem.EXTERNO).delete()
    EventoExterno.objects.bulk_create([
        EventoExterno(consultorio=conta.consultorio, conta=conta, origem=EventoExterno.Origem.EXTERNO, id_externo=e["id"], inicio=e["inicio"], fim=e["fim"])
        for e in remotos
    ])
    return len(remotos)


def sincronizar(conta, *, agora=None) -> bool:
    """Retorna True se sincronizou. Erros do provedor ficam em `ultimo_erro`."""
    agora = agora or timezone.now()
    janela_fim = agora + timedelta(days=settings.PSIQ_CALENDARIO_JANELA_DIAS)
    try:
        acesso = _token_valido(conta)
        _exportar(conta, acesso, agora, janela_fim)
        _importar(conta, acesso, agora, janela_fim)
    except (ErroProvedor, Exception) as exc:  # rede, token revogado, resposta inesperada
        logger.warning("Falha ao sincronizar calendario %s: %s", conta.pk, type(exc).__name__)
        conta.ultimo_erro = f"{type(exc).__name__}: {str(exc)[:150]}" if isinstance(exc, ErroProvedor) else type(exc).__name__
        conta.save(update_fields=["ultimo_erro", "atualizado_em"])
        return False
    conta.ultima_sincronizacao, conta.ultimo_erro = agora, ""
    conta.save(update_fields=["ultima_sincronizacao", "ultimo_erro", "atualizado_em"])
    return True


def desconectar(conta):
    """Remove da agenda externa os eventos que o PSIQ criou (melhor esforco) e apaga a conexao e os tokens."""
    try:
        acesso = _token_valido(conta)
        for evento in conta.eventos.filter(origem=EventoExterno.Origem.PSIQ):
            provedor_da(conta).apagar(acesso, evento.id_externo)
    except Exception as exc:  # a conexao e removida mesmo se o provedor estiver fora
        logger.warning("Nao foi possivel limpar eventos externos: %s", type(exc).__name__)
    conta.delete()


def bloqueado_externamente(profissional, inicio, fim) -> bool:
    """Compromisso pessoal importado que se sobrepoe ao intervalo (usado ao oferecer horarios livres)."""
    return EventoExterno.objects.filter(
        conta__profissional=profissional, conta__ativa=True, origem=EventoExterno.Origem.EXTERNO, inicio__lt=fim, fim__gt=inicio
    ).exists()
