from django.core.management.base import BaseCommand

from apps.core import cripto
from apps.contas.models import Usuario
from apps.prontuario.models import ChaveDados


class Command(BaseCommand):
    help = (
        "Depois de colocar a NOVA chave mestra na frente em PSIQ_CHAVE_MESTRA (nova,antiga), reescreve todas as "
        "chaves de dados com a chave primária. Só depois disso a chave antiga pode ser removida."
    )

    def handle(self, *args, **opcoes):
        total = 0
        for chave in ChaveDados.objects.all():
            chave.chave_cifrada = cripto.recifrar(chave.chave_cifrada)
            chave.save(update_fields=["chave_cifrada", "atualizado_em"])
            total += 1
        for usuario in Usuario.objects.exclude(segundo_fator_segredo_cifrado=""):
            usuario.segundo_fator_segredo_cifrado = cripto.recifrar(usuario.segundo_fator_segredo_cifrado)
            usuario.save(update_fields=["segundo_fator_segredo_cifrado", "atualizado_em"])
            total += 1
        self.stdout.write(self.style.SUCCESS(f"{total} segredo(s) reescrito(s) com a chave mestra primária."))
