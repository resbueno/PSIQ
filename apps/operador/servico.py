"""Operacao da plataforma: contagens de conta (sem conteudo clinico), cobranca manual, inadimplencia e suporte."""

from datetime import date, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil, Usuario, Vinculo
from apps.core.tenancy import contexto
from apps.pacientes.models import Paciente
from apps.plataforma.models import Consultorio, PagamentoPlataforma

from .models import AcessoSuporte


class ErroOperador(Exception):
    """Regra violada; a mensagem pode ser mostrada ao usuario."""


def metadados(consultorio):
    """Numeros de conta para o painel do operador. Nunca le conteudo clinico (so contagens de cadastro)."""
    with contexto(consultorio_id=consultorio.pk):
        pacientes = Paciente.objects.filter(consultorio=consultorio, ativo=True, mesclado_em__isnull=True).count()
        profissionais = Vinculo.objects.filter(consultorio=consultorio, ativo=True, perfil=Perfil.PROFISSIONAL).count()
        usuarios = Vinculo.objects.filter(consultorio=consultorio, ativo=True).count()
    pendentes = consultorio.pagamentos.filter(status=PagamentoPlataforma.Status.PENDENTE)
    atrasados = pendentes.filter(vencimento__lt=date.today())
    return {
        "pacientes": pacientes, "profissionais": profissionais, "usuarios": usuarios,
        "pendentes": pendentes.count(), "atrasados": atrasados.count(),
        "acima_do_limite": bool(consultorio.plano_id and pacientes > consultorio.plano.limite_pacientes_ativos),
    }


# --------------------------------------------------------------------------- cobranca manual e inadimplencia


def _emails_dos_admins(consultorio):
    with contexto(consultorio_id=consultorio.pk):
        usuarios = Usuario.objects.filter(
            vinculos__consultorio=consultorio, vinculos__perfil=Perfil.ADMIN, vinculos__ativo=True, is_active=True
        ).distinct()
        return [u.email for u in usuarios]


def _avisar(consultorio, assunto, corpo):
    destinatarios = _emails_dos_admins(consultorio)
    if destinatarios:
        send_mail(assunto, corpo, None, destinatarios, fail_silently=True)


def recalcular_situacao(consultorio, hoje: date | None = None):
    """Pendencia vencida: aviso e carencia (15 dias); depois somente leitura. Pago: volta ao normal.
    Nunca corte total: leitura e exportacao seguem liberadas. Retorna o novo status se mudou, senao None."""
    hoje = hoje or date.today()
    consultorio.refresh_from_db()
    if consultorio.status == Consultorio.Status.ENCERRADO:
        return None
    vencidos = consultorio.pagamentos.filter(status=PagamentoPlataforma.Status.PENDENTE, vencimento__lt=hoje).order_by("vencimento")
    mais_antigo = vencidos.first()
    if mais_antigo is None:
        novo = Consultorio.Status.ATIVO
    elif (hoje - mais_antigo.vencimento).days >= settings.MEUPSIQ_CARENCIA_DIAS:
        novo = Consultorio.Status.SOMENTE_LEITURA
    else:
        novo = Consultorio.Status.CARENCIA
    if novo == consultorio.status:
        return None
    consultorio.status = novo
    consultorio.save(update_fields=["status", "atualizado_em"])
    if novo == Consultorio.Status.CARENCIA:
        limite = mais_antigo.vencimento + timedelta(days=settings.MEUPSIQ_CARENCIA_DIAS)
        _avisar(consultorio, "Pagamento pendente do MeuPSIQ",
                f"Há um pagamento pendente do {consultorio.nome}. Regularize até {limite:%d/%m/%Y} para evitar que o acesso passe a somente leitura. "
                "Seus dados continuam disponíveis e a exportação nunca é bloqueada.")
    elif novo == Consultorio.Status.SOMENTE_LEITURA:
        _avisar(consultorio, "Acesso em somente leitura",
                f"O {consultorio.nome} passou a somente leitura por pendência de pagamento. Você continua vendo e exportando todos os dados; "
                "ao regularizar, o acesso completo volta na hora.")
    return novo


def criar_pagamento(consultorio, *, competencia: date, valor, vencimento: date):
    competencia = competencia.replace(day=1)
    if PagamentoPlataforma.objects.filter(consultorio=consultorio, competencia=competencia).exists():
        raise ErroOperador("Já existe cobrança para essa competência.")
    pagamento = PagamentoPlataforma.objects.create(consultorio=consultorio, competencia=competencia, valor=valor, vencimento=vencimento)
    recalcular_situacao(consultorio)
    return pagamento


@transaction.atomic
def marcar_pago(pagamento):
    if pagamento.status == PagamentoPlataforma.Status.PAGO:
        raise ErroOperador("Este pagamento já está marcado como pago.")
    pagamento.status, pagamento.pago_em = PagamentoPlataforma.Status.PAGO, timezone.now()
    pagamento.save(update_fields=["status", "pago_em", "atualizado_em"])
    return recalcular_situacao(pagamento.consultorio)


def mudar_plano(consultorio, plano):
    consultorio.plano = plano
    consultorio.save(update_fields=["plano", "atualizado_em"])


# --------------------------------------------------------------------------- suporte autorizado pelo cliente

DURACOES_SUPORTE_HORAS = (1, 4, 24)


def autorizar_suporte(request, operador, *, horas: int, motivo: str):
    if not operador.is_staff or not operador.is_active:
        raise ErroOperador("Esse usuário não é da equipe de suporte da plataforma.")
    if horas not in DURACOES_SUPORTE_HORAS:
        raise ErroOperador("Escolha uma duração válida.")
    if not motivo.strip():
        raise ErroOperador("Informe o motivo do suporte.")
    acesso = AcessoSuporte.objects.create(
        consultorio=request.consultorio, operador=operador, autorizado_por_id=request.user.pk,
        fim=timezone.now() + timedelta(hours=horas), motivo=motivo.strip()[:300],
    )
    auditoria.registrar(request, "suporte_autorizado", "acesso_suporte", acesso.pk, operador=str(operador.pk), horas=horas)
    return acesso


def revogar_suporte(request, acesso):
    acesso.revogado_em = timezone.now()
    acesso.save(update_fields=["revogado_em", "atualizado_em"])
    auditoria.registrar(request, "suporte_revogado", "acesso_suporte", acesso.pk)


def acesso_vigente(operador, consultorio_id):
    """O acesso de suporte vigente do operador neste consultorio (precisa do contexto do consultorio no banco)."""
    agora = timezone.now()
    return AcessoSuporte.objects.filter(
        operador=operador, consultorio_id=consultorio_id, revogado_em__isnull=True, inicio__lte=agora, fim__gt=agora
    ).first()


# --------------------------------------------------------------------------- novo consultorio


@transaction.atomic
def criar_consultorio_com_admin(*, nome, slug, documento="", plano=None, admin_nome, admin_email, admin_senha):
    """Cria o consultorio e o primeiro administrador. A escrita do vinculo exige contexto de RLS."""
    from django.contrib.auth.password_validation import validate_password

    email = admin_email.strip().lower()
    validate_password(admin_senha)
    if Usuario.objects.filter(email=email).exists():
        raise ErroOperador("Já existe um usuário com esse e-mail.")
    if Consultorio.objects.filter(slug=slug).exists():
        raise ErroOperador("Já existe um consultório com este link.")
    consultorio = Consultorio.objects.create(nome=nome, slug=slug, documento=documento, plano=plano)
    admin = Usuario.objects.create_user(email, admin_senha, nome=admin_nome)
    with contexto(consultorio_id=consultorio.pk, usuario_id=admin.pk):
        Vinculo.objects.create(usuario=admin, consultorio=consultorio, perfil=Perfil.ADMIN)
    return consultorio, admin
