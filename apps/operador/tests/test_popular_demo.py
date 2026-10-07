import pytest
from django.core.management import call_command

from apps.agenda.models import Consulta, SolicitacaoHorario
from apps.contas.models import Perfil, Usuario, Vinculo
from apps.core.tenancy import contexto
from apps.financeiro.models import AtendimentoConvenio, Lancamento, Recibo, Repasse
from apps.pacientes.models import Paciente
from apps.plataforma.models import Consultorio
from apps.prontuario.models import Documento, Prontuario, RegistroVersao

pytestmark = pytest.mark.django_db


def test_carga_demo_cria_todos_os_papeis_e_dados_coerentes(capsys, settings):
    call_command("popular_demo")
    saida = capsys.readouterr().out
    consultorio = Consultorio.objects.get(nome="Clínica Demo PSIQ")
    assert "Portal do paciente" in saida and str(consultorio.pk) in saida

    with contexto(consultorio_id=consultorio.pk):
        perfis = set(Vinculo.objects.filter(consultorio=consultorio).values_list("perfil", flat=True))
        assert perfis == {Perfil.ADMIN, Perfil.ASSISTENTE, Perfil.PROFISSIONAL}
        assert Vinculo.objects.filter(consultorio=consultorio, perfil=Perfil.PROFISSIONAL).count() == 2
        assert Paciente.objects.filter(consultorio=consultorio).count() == 5
        assert Consulta.objects.filter(consultorio=consultorio, status="realizada").count() >= 6
        assert Consulta.objects.filter(consultorio=consultorio, status="faltou").count() == 1
        assert SolicitacaoHorario.objects.filter(consultorio=consultorio, status="pendente").count() == 1
        assert Lancamento.objects.filter(consultorio=consultorio, tipo="convenio").count() == 1
        assert AtendimentoConvenio.objects.get().valor_glosado > 0
        assert Recibo.objects.filter(consultorio=consultorio).count() == 1
        assert Repasse.objects.filter(consultorio=consultorio).count() >= 3
        assert Prontuario.objects.filter(consultorio=consultorio).count() == 1
        assert RegistroVersao.objects.filter(consultorio=consultorio).count() == 3  # anamnese v1 e v2 + evolucao
        assert Documento.objects.filter(consultorio=consultorio, chave__isnull=False, liberado_ao_paciente=True).count() == 1

    operador = Usuario.objects.get(email="operador@demo.psiq.local")
    assert operador.is_staff and operador.segundo_fator_ativo
    for email in ("psicologa", "psiquiatra", "assistente"):
        assert Usuario.objects.get(email=f"{email}@demo.psiq.local").segundo_fator_ativo
    assert not Usuario.objects.get(email="admin@demo.psiq.local").is_staff


def test_carga_demo_e_idempotente(capsys):
    call_command("popular_demo")
    capsys.readouterr()
    call_command("popular_demo")
    assert "já existe" in capsys.readouterr().out
    assert Consultorio.objects.filter(nome="Clínica Demo PSIQ").count() == 1


def test_recusa_rodar_fora_do_debug_sem_forcar(settings):
    from django.core.management.base import CommandError

    settings.DEBUG = False
    with pytest.raises(CommandError, match="--forcar"):
        call_command("popular_demo")
