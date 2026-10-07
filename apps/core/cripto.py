"""Cifra de segredos com a chave mestra (Fernet).

A chave mestra fica fora do banco e fora do backup (variavel de ambiente ou cofre).
A criptografia por campo do prontuario (etapa 3) estende este modulo com chaves por profissional.
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    chave = settings.PSIQ_CHAVE_MESTRA
    if not chave:
        raise ImproperlyConfigured("Defina PSIQ_CHAVE_MESTRA para cifrar segredos.")
    return Fernet(chave.encode())


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def decifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Segredo cifrado inválido ou chave mestra incorreta.") from exc
