"""Camada de avisos com canais plugaveis (e-mail agora; push e WhatsApp entram como novos canais).

Regras de conteudo: texto neutro, sem citar o tipo de atendimento nem nada clinico.
O link de acao so confirma ou cancela aquela consulta (token assinado, veja `token_acao`).
"""

import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.utils import timezone

from .models import Aviso, Consulta

logger = logging.getLogger(__name__)

SAL_TOKEN = "psiq.acao-consulta"
VALIDADE_TOKEN_SEGUNDOS = 60 * 60 * 24 * 60


class Canal:
    nome = ""

    def disponivel(self, destinatario) -> bool:
        raise NotImplementedError

    def endereco(self, destinatario) -> str:
        raise NotImplementedError

    def enviar(self, endereco: str, assunto: str, corpo: str) -> None:
        raise NotImplementedError


class CanalEmail(Canal):
    nome = Aviso.Canal.EMAIL

    def disponivel(self, destinatario):
        return bool(destinatario["email"])

    def endereco(self, destinatario):
        return destinatario["email"]

    def enviar(self, endereco, assunto, corpo):
        send_mail(assunto, corpo, None, [endereco], fail_silently=False)


CANAIS = {canal.nome: canal for canal in (CanalEmail(),)}


def token_acao(consulta, paciente) -> str:
    return signing.dumps(
        {"c": str(consulta.pk), "k": str(consulta.consultorio_id), "p": str(paciente.pk)}, salt=SAL_TOKEN
    )


def ler_token_acao(token: str) -> dict:
    """Levanta signing.BadSignature se invalido ou expirado."""
    return signing.loads(token, salt=SAL_TOKEN, max_age=VALIDADE_TOKEN_SEGUNDOS)


def link_acao(consulta, paciente) -> str:
    return f"{settings.PSIQ_URL_BASE.rstrip('/')}/c/{token_acao(consulta, paciente)}/"


def participantes(consulta):
    if consulta.paciente_id:
        return [consulta.paciente]
    return [p.paciente for p in consulta.grupo.participantes.select_related("paciente")]


def destinatarios(consulta):
    """Quem recebe: o paciente adulto e os responsaveis que recebem avisos (menores so pelos responsaveis)."""
    vistos, resultado = set(), []

    def adicionar(nome, email, telefone, paciente):
        chave = (email.lower(), str(paciente.pk))
        if chave in vistos:
            return
        vistos.add(chave)
        resultado.append({"nome": nome, "email": email, "telefone": telefone, "paciente": paciente})

    for paciente in participantes(consulta):
        if not paciente.menor_de_idade:
            adicionar(paciente.nome, paciente.email, paciente.telefone, paciente)
        for resp in paciente.responsaveis.filter(recebe_avisos=True):
            adicionar(resp.nome, resp.email, resp.telefone, paciente)
    return resultado


def _quando(consulta):
    local = timezone.localtime(consulta.inicio, ZoneInfo(consulta.consultorio.fuso))
    return f"{local:%d/%m/%Y} às {local:%H:%M}"


def montar_mensagem(consulta, tipo, destinatario):
    nome = destinatario["nome"].split()[0] if destinatario["nome"] else ""
    quando = _quando(consulta)
    abertura = {
        Aviso.Tipo.CONFIRMACAO: f"Você tem um compromisso em {quando}.",
        Aviso.Tipo.LEMBRETE: f"Lembrete: você tem um compromisso em {quando}.",
        Aviso.Tipo.REMARCACAO: f"Seu compromisso foi remarcado para {quando}.",
        Aviso.Tipo.CANCELAMENTO: f"O compromisso de {quando} foi cancelado.",
    }[tipo]
    linhas = [f"Olá, {nome}." if nome else "Olá.", "", abertura]
    if tipo != Aviso.Tipo.CANCELAMENTO:
        if consulta.link_online:
            linhas += ["", f"Acesse no horário combinado: {consulta.link_online}"]
        linhas += ["", f"Para confirmar ou cancelar: {link_acao(consulta, destinatario['paciente'])}"]
        linhas += [f"Seu portal: {settings.PSIQ_URL_BASE.rstrip('/')}/portal/{consulta.consultorio_id}/entrar/"]
    linhas += ["", consulta.consultorio.nome]
    return f"Aviso de {consulta.consultorio.nome}", "\n".join(linhas)


def enviar(consulta, tipo):
    """Cria um registro de aviso por destinatario e canal e tenta enviar. Falhas ficam registradas, nunca quebram o fluxo."""
    consulta = Consulta.objects.select_related("consultorio", "paciente", "grupo").get(pk=consulta.pk)
    registros = []
    for destinatario in destinatarios(consulta):
        assunto, corpo = montar_mensagem(consulta, tipo, destinatario)
        for canal in CANAIS.values():
            if not canal.disponivel(destinatario):
                continue
            aviso = Aviso.objects.create(
                consultorio_id=consulta.consultorio_id,
                consulta=consulta,
                canal=canal.nome,
                tipo=tipo,
                destinatario=canal.endereco(destinatario),
            )
            try:
                canal.enviar(canal.endereco(destinatario), assunto, corpo)
                aviso.status, aviso.enviado_em = Aviso.Status.ENVIADO, timezone.now()
            except Exception as exc:  # canal externo: registrar e seguir
                logger.warning("Falha ao enviar aviso %s: %s", aviso.pk, type(exc).__name__)
                aviso.status, aviso.erro = Aviso.Status.FALHOU, type(exc).__name__
            aviso.save(update_fields=["status", "enviado_em", "erro", "atualizado_em"])
            registros.append(aviso)
    return registros
