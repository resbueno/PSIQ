import os
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.agenda.models import Aviso
from apps.auditoria.models import Auditoria
from apps.contas.models import Usuario
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio
from apps.relatorios.models import Exportacao


def verificar(agora=None):
    """Retorna a lista de alertas (texto curto, sem dados pessoais). Vazia = tudo bem."""
    agora = agora or timezone.now()
    ultima_hora = agora - timedelta(hours=1)
    alertas = []

    marcador = settings.PSIQ_BACKUP_MARCADOR
    if marcador:
        if not os.path.exists(marcador):
            alertas.append("Backup: o marcador de último backup não existe.")
        else:
            idade = agora.timestamp() - os.path.getmtime(marcador)
            if idade > settings.PSIQ_BACKUP_MAX_HORAS * 3600:
                alertas.append(f"Backup: último backup bem-sucedido há {int(idade // 3600)} h (limite {settings.PSIQ_BACKUP_MAX_HORAS} h).")
    else:
        alertas.append("Backup: PSIQ_BACKUP_MARCADOR não configurado, não há como saber se o backup roda.")

    suspeitas = Usuario.objects.filter(falhas_login__gte=settings.PSIQ_ALERTA_LOGINS_FALHOS).count()
    if suspeitas:
        alertas.append(f"Segurança: {suspeitas} conta(s) com muitas falhas de login seguidas.")

    for consultorio in Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO):
        with contexto(consultorio_id=consultorio.pk):
            falhas = Aviso.objects.filter(status=Aviso.Status.FALHOU, criado_em__gte=ultima_hora).count()
            if falhas >= settings.PSIQ_ALERTA_FALHAS_DE_AVISO:
                alertas.append(f"Avisos: {falhas} envios falharam na última hora em '{consultorio.nome}' (fila de e-mail ou provedor).")
            exportacoes = Exportacao.objects.filter(criado_em__gte=ultima_hora).count()
            if exportacoes >= settings.PSIQ_ALERTA_EXPORTACOES:
                alertas.append(f"Segurança: {exportacoes} exportações na última hora em '{consultorio.nome}'.")
            fora_da_janela = Auditoria.objects.filter(acao="suporte_requisicao", criado_em__gte=ultima_hora).exclude(
                objeto_id__in=[str(a.pk) for a in _acessos_vigentes(consultorio, agora)]
            ).count()
            if fora_da_janela:
                alertas.append(f"Segurança: {fora_da_janela} acesso(s) de suporte fora de uma autorização vigente em '{consultorio.nome}'.")
    return alertas


def _acessos_vigentes(consultorio, agora):
    from apps.operador.models import AcessoSuporte

    return AcessoSuporte.objects.filter(consultorio=consultorio, revogado_em__isnull=True, inicio__lte=agora, fim__gt=agora)


class Command(BaseCommand):
    help = "Verifica backup, avisos e sinais de ataque e alerta o operador por e-mail (PSIQ_ALERTA_EMAIL). Rode a cada 10 minutos (cron)."

    def handle(self, *args, **opcoes):
        alertas = verificar()
        if not alertas:
            self.stdout.write(self.style.SUCCESS("Tudo certo."))
            return
        for alerta in alertas:
            self.stdout.write(self.style.WARNING(alerta))
        if settings.PSIQ_ALERTA_EMAIL:
            send_mail("Alerta PSIQ", "\n".join(f"- {a}" for a in alertas), None, [settings.PSIQ_ALERTA_EMAIL], fail_silently=True)
