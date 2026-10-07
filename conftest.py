import pytest
from cryptography.fernet import Fernet

from apps.contas.models import Perfil, Profissional, Usuario, Vinculo
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio

import itertools

SENHA = "uma-senha-bem-longa-123"
_registros = itertools.count(1000)


@pytest.fixture(autouse=True)
def chave_mestra(settings):
    settings.PSIQ_CHAVE_MESTRA = Fernet.generate_key().decode()
    settings.SECURE_SSL_REDIRECT = False


@pytest.fixture
def criar_consultorio(db):
    def _criar(nome="Consultório Teste", **extra):
        return Consultorio.objects.create(nome=nome, **extra)

    return _criar


@pytest.fixture
def criar_usuario(db):
    """Cria usuario e vinculo (a escrita do vinculo exige contexto de RLS)."""

    def _criar(email, consultorio, perfil=Perfil.ASSISTENTE, nome="Fulano", com_2fa=False, registro=None):
        usuario = Usuario.objects.create_user(email, SENHA, nome=nome)
        if perfil == Perfil.PROFISSIONAL:
            Profissional.objects.create(
                usuario=usuario, tipo="psicologo", conselho="CRP", numero=registro or str(next(_registros)), uf="SP"
            )
        if com_2fa:
            import pyotp

            segredo = pyotp.random_base32()
            usuario.definir_segredo_2fa(segredo)
            usuario.segundo_fator_ativo = True
            usuario.save()
            usuario.segredo_teste = segredo
        with contexto(consultorio_id=consultorio.pk, usuario_id=usuario.pk):
            Vinculo.objects.create(usuario=usuario, consultorio=consultorio, perfil=perfil)
        return usuario

    return _criar


@pytest.fixture
def fazer_request(rf):
    """Request minimo para chamar servicos (a auditoria usa user, consultorio, META)."""

    def _fazer(usuario, consultorio):
        request = rf.get("/")
        request.user = usuario
        request.consultorio = consultorio
        request.vinculo = None
        return request

    return _fazer


@pytest.fixture
def criar_paciente(db):
    def _criar(consultorio, nome="Maria da Silva", **extra):
        from apps.pacientes.models import Paciente

        with contexto(consultorio_id=consultorio.pk):
            return Paciente.objects.create(consultorio=consultorio, nome=nome, **extra)

    return _criar


def entrar_com_2fa(client, usuario):
    """Login completo (senha + codigo TOTP) para usuarios com 2FA ativo."""
    import pyotp
    from django.urls import reverse

    client.post(reverse("contas:entrar"), {"email": usuario.email, "senha": SENHA})
    client.post(reverse("contas:verificar_2fa"), {"codigo": pyotp.TOTP(usuario.segredo_teste).now()})


@pytest.fixture(autouse=True)
def armazenamento_temporario(settings, tmp_path):
    settings.PSIQ_ANEXOS_DIR = str(tmp_path / "armazenamento")
