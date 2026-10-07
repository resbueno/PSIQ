"""Portal do paciente: acesso por codigo (sem senha), privacidade e pedidos do titular."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone

from apps.auditoria import servico as auditoria
from apps.pacientes.models import Paciente

from .models import CodigoAcesso, SolicitacaoLGPD


def _hash(codigo: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), codigo.encode(), hashlib.sha256).hexdigest()


def pacientes_do_email(consultorio, email):
    """Quem este e-mail pode acessar: o proprio paciente (a partir de 16 anos) e os dependentes de que e responsavel."""
    email = email.strip().lower()
    base = Paciente.objects.filter(consultorio=consultorio, ativo=True, mesclado_em__isnull=True)
    encontrados = {}
    for paciente in base.filter(email__iexact=email):
        minima = settings.PSIQ_PORTAL_IDADE_MINIMA_ACESSO_PROPRIO
        if paciente.idade is None or paciente.idade >= minima:
            encontrados[paciente.pk] = paciente
    for paciente in base.filter(responsaveis__email__iexact=email).distinct():
        encontrados[paciente.pk] = paciente
    return sorted(encontrados.values(), key=lambda p: p.nome)


def solicitar_codigo(request, consultorio, email) -> bool:
    """Envia um codigo se o e-mail corresponde a alguem. A resposta ao usuario e sempre a mesma (sem enumeracao)."""
    email = email.strip().lower()
    if not pacientes_do_email(consultorio, email):
        return False
    ultima_hora = timezone.now() - timedelta(hours=1)
    if CodigoAcesso.objects.filter(consultorio=consultorio, email=email, criado_em__gte=ultima_hora).count() >= settings.PSIQ_PORTAL_MAX_CODIGOS_POR_HORA:
        return False
    codigo = f"{secrets.randbelow(10**6):06d}"
    CodigoAcesso.objects.create(
        consultorio=consultorio, email=email, codigo_hash=_hash(codigo),
        expira_em=timezone.now() + timedelta(minutes=settings.PSIQ_PORTAL_CODIGO_VALIDADE_MINUTOS),
    )
    send_mail(
        f"Seu código de acesso - {consultorio.nome}",
        f"Seu código de acesso é {codigo}.\nEle vale por {settings.PSIQ_PORTAL_CODIGO_VALIDADE_MINUTOS} minutos e só pode ser usado uma vez.\n"
        "Se você não pediu este código, ignore esta mensagem.\n\n" + consultorio.nome,
        None, [email], fail_silently=True,
    )
    auditoria.registrar(request, "portal_codigo_enviado", "consultorio", consultorio.pk, consultorio_id=consultorio.pk)
    return True


def validar_codigo(request, consultorio, email, codigo):
    """Retorna a lista de pacientes acessiveis se o codigo estiver correto; None caso contrario."""
    email, codigo = email.strip().lower(), "".join(codigo.split())
    agora = timezone.now()
    registro = (
        CodigoAcesso.objects.filter(consultorio=consultorio, email=email, usado_em__isnull=True, expira_em__gt=agora)
        .order_by("-criado_em").first()
    )
    if registro is None or registro.tentativas >= settings.PSIQ_PORTAL_MAX_TENTATIVAS:
        return None
    if not hmac.compare_digest(registro.codigo_hash, _hash(codigo)):
        registro.tentativas += 1
        registro.save(update_fields=["tentativas", "atualizado_em"])
        auditoria.registrar(request, "portal_login_falha", "consultorio", consultorio.pk, consultorio_id=consultorio.pk)
        return None
    registro.usado_em = agora
    registro.save(update_fields=["usado_em", "atualizado_em"])
    pacientes = pacientes_do_email(consultorio, email)
    if not pacientes:
        return None
    auditoria.registrar(request, "portal_login", "consultorio", consultorio.pk, consultorio_id=consultorio.pk, pacientes=len(pacientes))
    return pacientes


def abrir_solicitacao_lgpd(request, paciente, tipo, mensagem=""):
    solicitacao = SolicitacaoLGPD.objects.create(
        consultorio_id=paciente.consultorio_id, paciente=paciente, tipo=tipo, mensagem=mensagem.strip()[:500]
    )
    auditoria.registrar(request, "lgpd_solicitacao_aberta", "solicitacao_lgpd", solicitacao.pk, consultorio_id=paciente.consultorio_id, tipo=tipo)
    return solicitacao


def atender_solicitacao_lgpd(request, solicitacao):
    solicitacao.status, solicitacao.atendida_por_id, solicitacao.atendida_em = (
        SolicitacaoLGPD.Status.ATENDIDA, request.user.pk, timezone.now(),
    )
    solicitacao.save()
    auditoria.registrar(request, "lgpd_solicitacao_atendida", "solicitacao_lgpd", solicitacao.pk)
