import pytest
from django.urls import reverse

from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil
from apps.core.tenancy import definir_contexto
from conftest import SENHA

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinica(criar_consultorio, criar_usuario):
    consultorio = criar_consultorio("Clínica Demo")
    admin = criar_usuario("admin@demo.com", consultorio, Perfil.ADMIN)
    assistente = criar_usuario("sec@demo.com", consultorio, Perfil.ASSISTENTE)
    return consultorio, admin, assistente


def entrar(client, email):
    client.post(reverse("contas:entrar"), {"email": email, "senha": SENHA})


def test_ver_como_e_404_fora_de_consultorio_de_demonstracao(client, clinica, settings):
    settings.MEUPSIQ_CONSULTORIOS_SEM_2FA = []
    _, admin, assistente = clinica
    entrar(client, admin.email)
    assert client.post(reverse("contas:ver_como", args=[assistente.pk])).status_code == 404


def test_admin_vira_assistente_e_volta_em_consultorio_de_demonstracao(client, clinica, settings):
    consultorio, admin, assistente = clinica
    settings.MEUPSIQ_CONSULTORIOS_SEM_2FA = [consultorio.slug]
    entrar(client, admin.email)
    assert client.post(reverse("contas:ver_como", args=[assistente.pk])).status_code == 302
    pagina = client.get(reverse("painel"))
    assert pagina.status_code == 200 and "ver como".encode() in pagina.content
    # como assistente nao pode usar a rota para virar outra pessoa que nao esteja no consultorio, mas volta ao admin
    assert client.post(reverse("contas:ver_como", args=[admin.pk])).status_code == 302
    definir_contexto(consultorio_id=consultorio.pk)
    assert Auditoria.objects.filter(acao="ver_como").count() == 2


def test_assistente_sem_ter_vindo_do_admin_nao_usa_ver_como(client, clinica, settings):
    consultorio, admin, assistente = clinica
    settings.MEUPSIQ_CONSULTORIOS_SEM_2FA = [consultorio.slug]
    entrar(client, assistente.email)
    assert client.post(reverse("contas:ver_como", args=[admin.pk])).status_code == 404
