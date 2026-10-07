"""Cifra de segredos e de dados clinicos.

- Chave mestra (`MEUPSIQ_CHAVE_MESTRA`): fora do banco e do backup. Aceita varias chaves separadas por virgula;
  a primeira cifra, todas decifram (rotacao sem parar o sistema).
- Chaves de dados (uma ativa por profissional): guardadas no banco cifradas pela mestra, usadas para o conteudo
  clinico e os arquivos. Ver `apps.prontuario.chaves`.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _mestra() -> MultiFernet:
    chaves = [c.strip() for c in settings.MEUPSIQ_CHAVE_MESTRA.split(",") if c.strip()]
    if not chaves:
        raise ImproperlyConfigured("Defina MEUPSIQ_CHAVE_MESTRA para cifrar segredos.")
    return MultiFernet([Fernet(c.encode()) for c in chaves])


def cifrar(texto: str) -> str:
    return _mestra().encrypt(texto.encode()).decode()


def decifrar(token: str) -> str:
    try:
        return _mestra().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Segredo cifrado inválido ou chave mestra incorreta.") from exc


def recifrar(token: str) -> str:
    """Reescreve o token com a chave mestra primaria (apos rotacao da mestra)."""
    return _mestra().rotate(token.encode()).decode()


def nova_chave_de_dados() -> str:
    return Fernet.generate_key().decode()


def cifrar_com(chave_de_dados: str, dados: bytes) -> bytes:
    return Fernet(chave_de_dados.encode()).encrypt(dados)


def decifrar_com(chave_de_dados: str, token: bytes) -> bytes:
    try:
        return Fernet(chave_de_dados.encode()).decrypt(token)
    except InvalidToken as exc:
        raise ValueError("Conteúdo cifrado inválido ou chave de dados incorreta.") from exc


def cifrar_bytes(dados: bytes) -> bytes:
    """Cifra pela chave mestra (arquivos sem conteudo clinico, ex.: declaracao de comparecimento)."""
    return _mestra().encrypt(dados)


def decifrar_bytes(token: bytes) -> bytes:
    try:
        return _mestra().decrypt(token)
    except InvalidToken as exc:
        raise ValueError("Arquivo cifrado inválido ou chave mestra incorreta.") from exc
