import re
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db


def test_paginas_tem_politica_de_conteudo_restritiva(client):
    politica = client.get("/entrar/")["Content-Security-Policy"]
    assert "script-src 'self'" in politica and "'unsafe-inline'" not in politica.split("style-src")[0]
    for diretiva in ("object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
        assert diretiva in politica


def test_respostas_de_arquivos_dinamicos_tambem_levam_a_politica(client):
    assert "Content-Security-Policy" in client.get("/manifest.webmanifest")
    assert "Content-Security-Policy" in client.get("/saude/")


def test_admin_do_django_fica_de_fora_da_politica(client):
    assert "Content-Security-Policy" not in client.get("/admin/login/")


def test_nenhum_template_usa_script_ou_manipulador_inline():
    """A CSP bloqueia script inline: qualquer um quebraria a tela em silencio."""
    raiz = Path(settings.BASE_DIR) / "templates"
    for arquivo in raiz.rglob("*.html"):
        texto = arquivo.read_text(encoding="utf-8")
        assert not re.search(r"\son(change|click|submit|load|input|focus|blur)\s*=", texto, re.I), f"manipulador inline em {arquivo}"
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", texto, re.I), f"script inline em {arquivo}"
