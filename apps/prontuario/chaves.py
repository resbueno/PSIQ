"""Chaves de dados por profissional. A de uso corrente e a unica `ativa`; as antigas continuam decifrando versoes antigas."""

from django.db import IntegrityError, transaction

from apps.core import cripto

from .models import ChaveDados


def chave_ativa(profissional) -> ChaveDados:
    chave = ChaveDados.objects.filter(profissional=profissional, ativa=True).first()
    if chave is None:
        try:
            with transaction.atomic():
                chave = ChaveDados.objects.create(
                    profissional=profissional, chave_cifrada=cripto.cifrar(cripto.nova_chave_de_dados())
                )
        except IntegrityError:  # outra requisicao criou ao mesmo tempo
            chave = ChaveDados.objects.get(profissional=profissional, ativa=True)
    return chave


def texto_da_chave(chave: ChaveDados) -> str:
    return cripto.decifrar(chave.chave_cifrada)


def cifrar_texto(chave: ChaveDados, texto: str) -> str:
    return cripto.cifrar_com(texto_da_chave(chave), texto.encode()).decode()


def decifrar_texto(chave: ChaveDados, token: str) -> str:
    return cripto.decifrar_com(texto_da_chave(chave), token.encode()).decode()


def cifrar_arquivo(chave: ChaveDados, dados: bytes) -> bytes:
    return cripto.cifrar_com(texto_da_chave(chave), dados)


def decifrar_arquivo(chave: ChaveDados, dados: bytes) -> bytes:
    return cripto.decifrar_com(texto_da_chave(chave), dados)


@transaction.atomic
def rotacionar(profissional) -> ChaveDados:
    """Cria uma nova chave ativa. Versoes e arquivos antigos seguem legiveis pela chave com que foram cifrados."""
    ChaveDados.objects.filter(profissional=profissional, ativa=True).update(ativa=False)
    return ChaveDados.objects.create(profissional=profissional, chave_cifrada=cripto.cifrar(cripto.nova_chave_de_dados()))
