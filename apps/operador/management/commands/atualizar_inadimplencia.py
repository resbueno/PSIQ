from django.core.management.base import BaseCommand

from apps.operador import servico
from apps.plataforma.models import Consultorio


class Command(BaseCommand):
    help = "Aplica a regra de inadimplência (aviso, carência de 15 dias, somente leitura). Rode todo dia (cron)."

    def handle(self, *args, **opcoes):
        mudancas = 0
        for consultorio in Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO):
            if servico.recalcular_situacao(consultorio):
                mudancas += 1
        self.stdout.write(self.style.SUCCESS(f"{mudancas} consultório(s) com situação alterada."))
