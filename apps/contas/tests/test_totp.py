import time

import pyotp
import pytest

from apps.contas import totp
from apps.contas.models import Usuario

SEGREDO = pyotp.random_base32()


def test_codigo_do_passo_atual_anterior_e_seguinte_vale_uma_vez():
    agora = 1_700_000_000
    gerador = pyotp.TOTP(SEGREDO)
    passo = agora // 30
    assert totp.passo_valido(SEGREDO, gerador.at(agora), agora=agora) == passo
    assert totp.passo_valido(SEGREDO, gerador.at(agora - 30), agora=agora) == passo - 1
    assert totp.passo_valido(SEGREDO, gerador.at(agora + 30), agora=agora) == passo + 1
    assert totp.passo_valido(SEGREDO, gerador.at(agora + 120), agora=agora) is None  # fora da janela
    assert totp.passo_valido(SEGREDO, "000000", agora=agora) is None


def test_passo_ja_usado_ou_anterior_e_recusado():
    agora = 1_700_000_000
    codigo = pyotp.TOTP(SEGREDO).at(agora)
    passo = agora // 30
    assert totp.passo_valido(SEGREDO, codigo, ultimo_passo=passo - 1, agora=agora) == passo
    assert totp.passo_valido(SEGREDO, codigo, ultimo_passo=passo, agora=agora) is None
    assert totp.passo_valido(SEGREDO, codigo, ultimo_passo=passo + 5, agora=agora) is None


@pytest.mark.django_db
def test_consumir_aceita_uma_vez_e_recusa_o_reuso():
    usuario = Usuario.objects.create_user("t@x.com", "uma-senha-bem-longa-123", nome="T")
    usuario.definir_segredo_2fa(SEGREDO)
    usuario.save()
    codigo = pyotp.TOTP(SEGREDO).now()
    assert totp.consumir(usuario, codigo) is True
    assert totp.consumir(usuario, codigo) is False  # mesmo codigo, mesmo passo
    usuario.refresh_from_db()
    assert usuario.ultimo_passo_totp == int(time.time() // 30)


@pytest.mark.django_db
def test_duas_requisicoes_com_o_mesmo_codigo_so_uma_passa():
    """O registro do passo e um UPDATE condicional: a segunda instancia da mesma conta nao consegue gravar."""
    usuario = Usuario.objects.create_user("t2@x.com", "uma-senha-bem-longa-123", nome="T")
    usuario.definir_segredo_2fa(SEGREDO)
    usuario.save()
    outra_copia = Usuario.objects.get(pk=usuario.pk)  # outra requisicao, lida antes de a primeira gravar
    codigo = pyotp.TOTP(SEGREDO).now()
    assert totp.consumir(usuario, codigo) is True
    assert totp.consumir(outra_copia, codigo) is False
