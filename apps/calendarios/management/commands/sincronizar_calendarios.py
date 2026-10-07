from django.core.management.base import BaseCommand

from apps.calendarios import sync
from apps.calendarios.models import ContaCalendario
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio


class Command(BaseCommand):
    help = "Sincroniza todos os calendários externos conectados. Rode a cada 10 minutos (cron)."

    def handle(self, *args, **opcoes):
        ok = falha = 0
        for consultorio in Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO):
            with contexto(consultorio_id=consultorio.pk):
                for conta in ContaCalendario.objects.filter(consultorio=consultorio, ativa=True).select_related("profissional", "consultorio"):
                    if sync.sincronizar(conta):
                        ok += 1
                    else:
                        falha += 1
        self.stdout.write(self.style.SUCCESS(f"{ok} calendário(s) sincronizado(s), {falha} com falha."))
