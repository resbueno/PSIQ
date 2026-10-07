from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.agenda import avisos
from apps.agenda.models import Aviso, Consulta
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio


class Command(BaseCommand):
    help = "Envia o lembrete das consultas que começam nas próximas N horas (padrão 24). Rode a cada 15 minutos (cron)."

    def add_arguments(self, parser):
        parser.add_argument("--horas", type=int, default=24)

    def handle(self, *args, **opcoes):
        agora = timezone.now()
        limite = agora + timedelta(hours=opcoes["horas"])
        total = 0
        for consultorio in Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO):
            with contexto(consultorio_id=consultorio.pk):
                ja_avisadas = Aviso.objects.filter(tipo=Aviso.Tipo.LEMBRETE).values("consulta_id")
                pendentes = Consulta.objects.filter(
                    status__in=(Consulta.Status.AGENDADA, Consulta.Status.CONFIRMADA),
                    inicio__gt=agora,
                    inicio__lte=limite,
                ).exclude(pk__in=ja_avisadas)
                for consulta in pendentes:
                    if avisos.enviar(consulta, Aviso.Tipo.LEMBRETE):
                        total += 1
        self.stdout.write(self.style.SUCCESS(f"{total} consulta(s) com lembrete enviado."))
