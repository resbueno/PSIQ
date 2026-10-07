from datetime import date

from django.core.management.base import BaseCommand
from django.test import RequestFactory

from apps.core.tenancy import contexto
from apps.financeiro import servico
from apps.plataforma.models import Consultorio


class Command(BaseCommand):
    help = "Gera o lançamento do mês para cada cobrança recorrente (aluguel de sala). Pode rodar todo dia: não duplica."

    def handle(self, *args, **opcoes):
        total = 0
        for consultorio in Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO):
            request = RequestFactory().get("/")
            request.user = None
            request.consultorio = consultorio
            with contexto(consultorio_id=consultorio.pk):
                total += len(servico.gerar_cobrancas_do_mes(request, consultorio, date.today()))
        self.stdout.write(self.style.SUCCESS(f"{total} cobrança(s) gerada(s)."))
