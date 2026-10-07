import pytest
from cryptography.fernet import Fernet

from apps.contas.models import Perfil, Profissional, Usuario, Vinculo
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio

SENHA = "uma-senha-bem-longa-123"


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
                usuario=usuario, tipo="psicologo", conselho="CRP", numero=registro or str(abs(hash(email)))[:6], uf="SP"
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
