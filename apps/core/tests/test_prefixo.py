"""O sistema funciona atras de um proxy que serve tudo sob um prefixo (ex.: /psiq) e remove o prefixo da requisicao."""

import pytest
from django.urls import reverse

from apps.contas.models import Perfil
from conftest import SENHA

pytestmark = pytest.mark.django_db


@pytest.fixture
def prefixo(settings):
    settings.FORCE_SCRIPT_NAME = "/psiq"
    from django.urls import set_script_prefix

    set_script_prefix("/psiq")
    yield "/psiq"
    set_script_prefix("/")


def test_enderecos_gerados_levam_o_prefixo(client, prefixo):
    pagina = client.get("/entrar/")  # o proxy ja removeu /psiq
    corpo = pagina.content.decode()
    assert pagina.status_code == 200
    assert 'href="/psiq/manifest.webmanifest"' in corpo and '<meta name="sw" content="/psiq/sw.js">' in corpo
    assert reverse("contas:entrar") == "/psiq/entrar/"


def test_redirecionamentos_voltam_com_o_prefixo(client, prefixo, criar_consultorio, criar_usuario):
    criar_usuario("sec@a.com", criar_consultorio("Clínica"), Perfil.ASSISTENTE)
    login = client.post("/entrar/", {"email": "sec@a.com", "senha": SENHA})
    assert login.status_code == 302 and login.url == "/psiq/"
    assert client.get("/").status_code == 200


def test_protecoes_por_caminho_continuam_valendo_com_prefixo(client, prefixo, criar_consultorio, criar_usuario):
    """Os middlewares comparam o caminho SEM o prefixo: equipe sem 2FA continua presa na tela de ativar o 2FA."""
    from apps.contas.models import Usuario

    Usuario.objects.create_user("op@psiq.com", SENHA, nome="Op", is_staff=True)
    client.post("/entrar/", {"email": "op@psiq.com", "senha": SENHA})
    resposta = client.get("/operador/")
    assert resposta.status_code == 302 and resposta.url == "/psiq/conta/2fa/configurar/"
    livre = client.get("/conta/2fa/configurar/")
    assert livre.status_code == 200


def test_service_worker_e_manifesto_com_escopo_do_prefixo(client, prefixo):
    sw = client.get("/sw.js")
    assert sw["Service-Worker-Allowed"] == "/psiq/"  # o navegador pede /psiq/sw.js; o escopo e a pasta do script
    manifesto = client.get("/manifest.webmanifest").json()
    assert manifesto["start_url"] == "/psiq/" and manifesto["scope"] == "/psiq/"


def test_cookies_tem_nome_proprio(client, db, settings):
    client.get("/entrar/")
    assert settings.SESSION_COOKIE_NAME == "psiq_sessionid" and settings.CSRF_COOKIE_NAME == "psiq_csrftoken"
    assert "psiq_csrftoken" in client.cookies


def test_redirect_uri_do_oauth_nao_duplica_o_prefixo(prefixo, settings):
    from apps.calendarios.views import _redirect_uri

    settings.PSIQ_URL_BASE = "https://homolog.exemplo.com/psiq"
    assert _redirect_uri("google") == "https://homolog.exemplo.com/psiq/calendarios/retorno/google/"
