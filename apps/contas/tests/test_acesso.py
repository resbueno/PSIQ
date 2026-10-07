"""Login, 2FA, bloqueio, permissoes por perfil e isolamento nas rotas."""

import pyotp
import pytest
from django.urls import reverse

from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil, SessaoDispositivo, Usuario, Vinculo
from apps.core.tenancy import contexto
from conftest import SENHA

pytestmark = pytest.mark.django_db


@pytest.fixture
def consultorio(criar_consultorio):
    return criar_consultorio("Clínica Aurora")


def entrar(client, email, senha=SENHA):
    return client.post(reverse("contas:entrar"), {"email": email, "senha": senha})


def test_login_sem_2fa_vai_ao_painel_e_audita(client, consultorio, criar_usuario):
    criar_usuario("sec@exemplo.com", consultorio, Perfil.ASSISTENTE)
    resposta = entrar(client, "sec@exemplo.com")
    assert resposta.status_code == 302 and resposta.url == reverse("painel")
    assert client.get(reverse("painel")).status_code == 200
    with contexto(consultorio_id=consultorio.pk):
        assert Auditoria.objects.filter(acao="login").count() == 1
    assert SessaoDispositivo.objects.count() == 1


def test_senha_errada_nao_entra_e_audita(client, consultorio, criar_usuario):
    criar_usuario("sec@exemplo.com", consultorio)
    resposta = entrar(client, "sec@exemplo.com", "errada")
    assert resposta.status_code == 401
    assert client.get(reverse("painel")).status_code == 302
    assert Auditoria.objects.filter(acao="login_falha").count() == 0  # RLS: falha sem consultorio nao e legivel ao app


def test_bloqueio_progressivo_apos_falhas(client, consultorio, criar_usuario, settings):
    usuario = criar_usuario("sec@exemplo.com", consultorio)
    for _ in range(settings.PSIQ_LOGIN_FALHAS_ANTES_DO_BLOQUEIO):
        entrar(client, "sec@exemplo.com", "errada")
    usuario.refresh_from_db()
    assert usuario.bloqueado
    resposta = entrar(client, "sec@exemplo.com")  # senha certa, mas bloqueado
    assert resposta.status_code == 429


def test_profissional_sem_2fa_e_forcado_a_configurar(client, consultorio, criar_usuario):
    criar_usuario("dra@exemplo.com", consultorio, Perfil.PROFISSIONAL)
    entrar(client, "dra@exemplo.com")
    resposta = client.get(reverse("painel"))
    assert resposta.status_code == 302 and resposta.url == reverse("contas:configurar_2fa")


def test_ativar_2fa_e_entrar_com_codigo(client, consultorio, criar_usuario):
    usuario = criar_usuario("dra@exemplo.com", consultorio, Perfil.PROFISSIONAL)
    entrar(client, "dra@exemplo.com")
    assert client.get(reverse("contas:configurar_2fa")).status_code == 200
    segredo = client.session["novo_segredo_2fa"]
    resposta = client.post(reverse("contas:configurar_2fa"), {"codigo": pyotp.TOTP(segredo).now()})
    assert resposta.status_code == 302
    usuario.refresh_from_db()
    assert usuario.segundo_fator_ativo and usuario.segredo_2fa == segredo
    assert segredo not in usuario.segundo_fator_segredo_cifrado  # guardado cifrado

    client.post(reverse("contas:sair"))
    resposta = entrar(client, "dra@exemplo.com")
    assert resposta.url == reverse("contas:verificar_2fa")
    assert client.get(reverse("painel")).status_code == 302  # ainda nao autenticado
    errado = client.post(reverse("contas:verificar_2fa"), {"codigo": "000000"})
    assert errado.status_code == 200
    certo = client.post(reverse("contas:verificar_2fa"), {"codigo": pyotp.TOTP(segredo).now()})
    assert certo.status_code == 302
    assert client.get(reverse("painel")).status_code == 200


def test_assistente_nao_acessa_area_do_admin(client, consultorio, criar_usuario):
    criar_usuario("sec@exemplo.com", consultorio, Perfil.ASSISTENTE)
    entrar(client, "sec@exemplo.com")
    for nome in ("contas:usuarios", "contas:novo_usuario", "contas:auditoria"):
        assert client.get(reverse(nome)).status_code == 403


def test_admin_cria_usuario_e_audita(client, consultorio, criar_usuario):
    criar_usuario("adm@exemplo.com", consultorio, Perfil.ADMIN)
    entrar(client, "adm@exemplo.com")
    resposta = client.post(
        reverse("contas:novo_usuario"),
        {"nome": "Nova Pessoa", "email": "NOVA@exemplo.com", "perfil": "assistente", "senha": "outra-senha-longa-456"},
    )
    assert resposta.status_code == 302
    novo = Usuario.objects.get(email="nova@exemplo.com")
    with contexto(consultorio_id=consultorio.pk, usuario_id=novo.pk):
        assert Vinculo.objects.filter(usuario=novo, consultorio=consultorio, perfil="assistente").exists()
        assert Auditoria.objects.filter(acao="vinculo_criado").exists()


def test_profissional_novo_exige_registro_no_conselho(client, consultorio, criar_usuario):
    criar_usuario("adm@exemplo.com", consultorio, Perfil.ADMIN)
    entrar(client, "adm@exemplo.com")
    resposta = client.post(
        reverse("contas:novo_usuario"),
        {"nome": "Dr. X", "email": "x@exemplo.com", "perfil": "profissional", "senha": "outra-senha-longa-456"},
    )
    assert resposta.status_code == 200
    assert not Usuario.objects.filter(email="x@exemplo.com").exists()


def test_admin_nao_altera_vinculo_de_outro_consultorio(client, consultorio, criar_consultorio, criar_usuario):
    outro = criar_consultorio("Outra Clínica")
    criar_usuario("adm@exemplo.com", consultorio, Perfil.ADMIN)
    alheio = criar_usuario("alheio@exemplo.com", outro, Perfil.ASSISTENTE)
    with contexto(consultorio_id=outro.pk):
        vinculo_alheio = Vinculo.objects.get(usuario=alheio)
    entrar(client, "adm@exemplo.com")
    resposta = client.post(reverse("contas:alternar_vinculo", args=[vinculo_alheio.pk]))
    assert resposta.status_code == 404
    with contexto(consultorio_id=outro.pk):
        assert Vinculo.objects.get(pk=vinculo_alheio.pk).ativo


def test_lista_de_usuarios_nao_mostra_outro_consultorio(client, consultorio, criar_consultorio, criar_usuario):
    outro = criar_consultorio("Outra Clínica")
    criar_usuario("adm@exemplo.com", consultorio, Perfil.ADMIN)
    criar_usuario("alheio@exemplo.com", outro, Perfil.ASSISTENTE)
    entrar(client, "adm@exemplo.com")
    assert b"alheio@exemplo.com" not in client.get(reverse("contas:usuarios")).content


def test_consultorio_somente_leitura_bloqueia_escrita(client, consultorio, criar_usuario):
    consultorio.status = "somente_leitura"
    consultorio.save()
    criar_usuario("adm@exemplo.com", consultorio, Perfil.ADMIN)
    entrar(client, "adm@exemplo.com")
    assert client.get(reverse("contas:usuarios")).status_code == 200
    resposta = client.post(reverse("contas:novo_usuario"), {"nome": "A", "email": "a@a.com", "perfil": "assistente"})
    assert resposta.status_code == 403


def test_revogar_dispositivo_encerra_a_sessao(client, consultorio, criar_usuario):
    from django.test import Client

    criar_usuario("sec@exemplo.com", consultorio)
    outro_aparelho = Client()
    entrar(client, "sec@exemplo.com")
    entrar(outro_aparelho, "sec@exemplo.com")
    assert outro_aparelho.get(reverse("painel")).status_code == 200

    sessao_do_outro = SessaoDispositivo.objects.get(session_key=outro_aparelho.session.session_key)
    resposta = client.post(reverse("contas:revogar_dispositivo", args=[sessao_do_outro.pk]))
    assert resposta.status_code == 302
    assert outro_aparelho.get(reverse("painel")).status_code == 302  # caiu para o login


def test_usuario_com_dois_consultorios_escolhe_um(client, consultorio, criar_consultorio, criar_usuario):
    outro = criar_consultorio("Segunda Clínica")
    usuario = criar_usuario("multi@exemplo.com", consultorio, Perfil.ASSISTENTE)
    with contexto(consultorio_id=outro.pk, usuario_id=usuario.pk):
        Vinculo.objects.create(usuario=usuario, consultorio=outro, perfil=Perfil.ADMIN)
    resposta = entrar(client, "multi@exemplo.com")
    assert resposta.url == reverse("contas:escolher_consultorio")
    assert client.get(reverse("painel")).status_code == 302
    client.post(reverse("contas:escolher_consultorio"), {"consultorio_id": str(outro.pk)})
    assert client.get(reverse("contas:usuarios")).status_code == 200  # admin na segunda clinica
    # consultorio que nao e dele e recusado
    client.post(reverse("contas:escolher_consultorio"), {"consultorio_id": "00000000-0000-0000-0000-000000000000"})
    assert client.get(reverse("contas:usuarios")).status_code == 200
