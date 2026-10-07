"""Armazenamento de arquivos ja cifrados. Hoje em disco local; um backend S3-compativel entra com a mesma interface
(salvar, ler, apagar) quando a infraestrutura for definida (docs/02-arquitetura.md, secao 5)."""

import uuid
from pathlib import Path

from django.conf import settings


class ArmazenamentoLocal:
    def __init__(self, base=None):
        self.base = Path(base or settings.PSIQ_ANEXOS_DIR).resolve()

    def _caminho(self, chave: str) -> Path:
        destino = (self.base / chave).resolve()
        if self.base not in destino.parents:
            raise ValueError("Caminho de armazenamento inválido.")
        return destino

    def salvar(self, conteudo_cifrado: bytes) -> str:
        nome = uuid.uuid4().hex
        chave = f"{nome[:2]}/{nome[2:4]}/{nome}.bin"  # nome opaco: nada do arquivo original no caminho
        destino = self._caminho(chave)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo_cifrado)
        return chave

    def ler(self, chave: str) -> bytes:
        return self._caminho(chave).read_bytes()

    def apagar(self, chave: str) -> None:
        self._caminho(chave).unlink(missing_ok=True)


def armazenamento() -> ArmazenamentoLocal:
    return ArmazenamentoLocal()
