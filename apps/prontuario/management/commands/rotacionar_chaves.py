from django.core.management.base import BaseCommand

from apps.contas.models import Profissional
from apps.prontuario import chaves
from apps.prontuario.models import ChaveDados


class Command(BaseCommand):
    help = (
        "Rotação periódica: cria uma nova chave de dados ativa para cada profissional que já tem chave. "
        "Versões e arquivos antigos continuam legíveis pela chave com que foram cifrados."
    )

    def handle(self, *args, **opcoes):
        total = 0
        for profissional in Profissional.objects.filter(pk__in=ChaveDados.objects.filter(ativa=True).values("profissional_id")):
            chaves.rotacionar(profissional)
            total += 1
        self.stdout.write(self.style.SUCCESS(f"{total} chave(s) rotacionada(s)."))
