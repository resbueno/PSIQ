"""Verificacao de codigo TOTP que nao aceita o mesmo codigo duas vezes (anti-reuso)."""

import hmac
import time

import pyotp
from django.db.models import Q

PASSO_SEGUNDOS = 30


def passo_valido(segredo: str, codigo: str, ultimo_passo=None, agora=None):
    """Retorna o numero do passo de 30 s em que `codigo` confere (janela de +-1 passo), ou None.
    Um passo igual ou anterior a `ultimo_passo` ja foi usado e e recusado."""
    atual = int((agora if agora is not None else time.time()) // PASSO_SEGUNDOS)
    totp = pyotp.TOTP(segredo)
    for passo in (atual - 1, atual, atual + 1):
        if hmac.compare_digest(totp.at(passo * PASSO_SEGUNDOS), codigo):
            if ultimo_passo is not None and passo <= ultimo_passo:
                return None
            return passo
    return None


def consumir(usuario, codigo: str) -> bool:
    """Confere o codigo e registra o passo usado de forma atomica (duas requisicoes simultaneas com o mesmo
    codigo: so uma passa)."""
    from .models import Usuario

    passo = passo_valido(usuario.segredo_2fa, codigo, usuario.ultimo_passo_totp)
    if passo is None:
        return False
    gravados = Usuario.objects.filter(pk=usuario.pk).filter(
        Q(ultimo_passo_totp__isnull=True) | Q(ultimo_passo_totp__lt=passo)
    ).update(ultimo_passo_totp=passo)
    if gravados:
        usuario.ultimo_passo_totp = passo
    return bool(gravados)
