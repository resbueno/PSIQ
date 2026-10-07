from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.agenda import servico
from apps.agenda.models import AgendaRegra, Consulta, SolicitacaoHorario
from apps.agenda.servico import ErroAgenda
from apps.contas.models import Perfil
from apps.core.tenancy import contexto

pytestmark = pytest.mark.django_db

SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def cenario(criar_consultorio, criar_usuario, criar_paciente):
    consultorio = criar_consultorio("Clínica A")
    doutora = criar_usuario("dra@a.com", consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    assistente = criar_usuario("sec@a.com", consultorio, Perfil.ASSISTENTE)
    paciente = criar_paciente(consultorio, "Maria da Silva", email="maria@x.com")

    class Cenario:
        pass

    c = Cenario()
    c.consultorio, c.profissional, c.assistente, c.paciente, c.usuario_prof = (
        consultorio, doutora.profissional, assistente, paciente, doutora,
    )
    with contexto(consultorio_id=consultorio.pk):
        yield c


def amanha(hora=10, dias=1):
    d = timezone.now().astimezone(SP).date() + timedelta(days=dias)
    return datetime.combine(d, time(hora, 0), tzinfo=SP)


def agendar(cenario, fazer_request, inicio=None, **extra):
    request = fazer_request(cenario.assistente, cenario.consultorio)
    parametros = dict(profissional=cenario.profissional, inicio=inicio or amanha(), duracao_minutos=50,
                      tipo="presencial", paciente=cenario.paciente)
    parametros.update(extra)
    return servico.agendar(request, **parametros), request


def test_agendar_cria_consulta_e_marca_primeira(cenario, fazer_request):
    (c1,), _ = agendar(cenario, fazer_request)
    assert c1.status == Consulta.Status.AGENDADA and c1.primeira_consulta
    (c2,), _ = agendar(cenario, fazer_request, inicio=amanha(14))
    assert not c2.primeira_consulta


def test_agendar_vincula_o_profissional_ao_paciente(cenario, fazer_request):
    agendar(cenario, fazer_request)
    from apps.pacientes.models import ProfissionalPaciente

    assert ProfissionalPaciente.objects.filter(paciente=cenario.paciente, profissional=cenario.profissional).exists()


def test_consulta_online_gera_link_jitsi_unico(cenario, fazer_request):
    (a,), _ = agendar(cenario, fazer_request, tipo="online")
    (b,), _ = agendar(cenario, fazer_request, tipo="online", inicio=amanha(15))
    assert a.link_online.startswith("https://meet.jit.si/psiq-") and a.link_online != b.link_online
    (presencial,), _ = agendar(cenario, fazer_request, inicio=amanha(16))
    assert presencial.link_online == ""


def test_conflito_de_horario_e_recusado(cenario, fazer_request):
    agendar(cenario, fazer_request)
    with pytest.raises(ErroAgenda, match="já tem um compromisso"):
        agendar(cenario, fazer_request, inicio=amanha() + timedelta(minutes=30))
    agendar(cenario, fazer_request, inicio=amanha() + timedelta(minutes=50))  # encostar e permitido


def test_cancelada_libera_o_horario(cenario, fazer_request):
    (c,), request = agendar(cenario, fazer_request)
    servico.cancelar(request, c, Consulta.CanceladoPor.EQUIPE)
    agendar(cenario, fazer_request)


def test_serie_semanal_e_cancelar_uma_ocorrencia_nao_apaga_a_serie(cenario, fazer_request):
    criadas, request = agendar(cenario, fazer_request, repetir_cada_semanas=1, repeticoes=4)
    assert len(criadas) == 4 and len({c.serie_id for c in criadas}) == 1
    assert [c.inicio - criadas[0].inicio for c in criadas] == [timedelta(weeks=i) for i in range(4)]
    servico.cancelar(request, criadas[1], Consulta.CanceladoPor.EQUIPE)
    assert Consulta.objects.filter(serie=criadas[0].serie, status__in=Consulta.ATIVAS).count() == 3


def test_serie_com_conflito_nao_cria_nada(cenario, fazer_request):
    agendar(cenario, fazer_request, inicio=amanha(dias=15))
    with pytest.raises(ErroAgenda):
        agendar(cenario, fazer_request, repetir_cada_semanas=1, repeticoes=4)  # a 3a ocorrencia bate (dia 15)
    assert Consulta.objects.count() == 1


def test_paciente_de_outro_consultorio_e_recusado(cenario, fazer_request, criar_consultorio, criar_paciente):
    outro = criar_consultorio("Clínica B")
    alheio = criar_paciente(outro, "Alheio")
    with pytest.raises(ErroAgenda, match="não pertence"):
        agendar(cenario, fazer_request, paciente=alheio)


def test_profissional_de_outro_consultorio_e_recusado(cenario, fazer_request, criar_consultorio, criar_usuario):
    outro = criar_consultorio("Clínica B")
    estranho = criar_usuario("estranho@b.com", outro, Perfil.PROFISSIONAL, com_2fa=True)
    with pytest.raises(ErroAgenda, match="não atende"):
        agendar(cenario, fazer_request, profissional=estranho.profissional)


def test_paciente_mesclado_nao_agenda(cenario, fazer_request, criar_paciente):
    principal = criar_paciente(cenario.consultorio, "Principal")
    from apps.pacientes.servico import mesclar

    mesclar(fazer_request(cenario.assistente, cenario.consultorio), principal, cenario.paciente)
    cenario.paciente.refresh_from_db()
    with pytest.raises(ErroAgenda, match="mesclado"):
        agendar(cenario, fazer_request)


def test_exatamente_paciente_ou_grupo(cenario, fazer_request):
    with pytest.raises(ErroAgenda):
        agendar(cenario, fazer_request, paciente=None)


def test_cancelamento_pelo_paciente_respeita_o_prazo(cenario, fazer_request):
    (longe,), request = agendar(cenario, fazer_request, inicio=amanha(dias=3))
    servico.cancelar(request, longe, Consulta.CanceladoPor.PACIENTE)
    assert longe.status == Consulta.Status.CANCELADA and not longe.falta_tardia

    (perto,), _ = agendar(cenario, fazer_request, inicio=timezone.now() + timedelta(hours=3))
    with pytest.raises(ErroAgenda, match="24 horas"):
        servico.cancelar(request, perto, Consulta.CanceladoPor.PACIENTE)
    servico.cancelar(request, perto, Consulta.CanceladoPor.EQUIPE, tardia=True)
    assert perto.falta_tardia


def test_prazo_de_cancelamento_e_configuravel(cenario, fazer_request):
    cenario.consultorio.politica_cancelamento_horas = 2
    cenario.consultorio.save()
    (c,), request = agendar(cenario, fazer_request, inicio=timezone.now() + timedelta(hours=3))
    c = Consulta.objects.select_related("consultorio").get(pk=c.pk)
    servico.cancelar(request, c, Consulta.CanceladoPor.PACIENTE)


def test_confirmar_e_realizada_atualizam_estado(cenario, fazer_request):
    passado = timezone.now() - timedelta(hours=2)
    (c,), request = agendar(cenario, fazer_request, inicio=passado)
    servico.confirmar(request, c)
    assert c.status == Consulta.Status.CONFIRMADA
    servico.marcar_realizada(request, c)
    cenario.paciente.refresh_from_db()
    assert c.status == Consulta.Status.REALIZADA and cenario.paciente.ultimo_atendimento_em == c.inicio


def test_nao_marca_realizada_consulta_futura(cenario, fazer_request):
    (c,), request = agendar(cenario, fazer_request)
    with pytest.raises(ErroAgenda, match="ainda não começou"):
        servico.marcar_realizada(request, c)


def test_remarcar_mantem_duracao_e_checa_conflito(cenario, fazer_request):
    (c,), request = agendar(cenario, fazer_request)
    (outra,), _ = agendar(cenario, fazer_request, inicio=amanha(15))
    with pytest.raises(ErroAgenda):
        servico.remarcar(request, c, amanha(15))
    servico.remarcar(request, c, amanha(11))
    assert c.inicio == amanha(11) and c.fim - c.inicio == timedelta(minutes=50)


def test_primeira_consulta_exige_aprovacao_e_retorno_e_livre(cenario, fazer_request):
    assert servico.exige_aprovacao(cenario.paciente, cenario.profissional)
    agendar(cenario, fazer_request)
    assert not servico.exige_aprovacao(cenario.paciente, cenario.profissional)
    cenario.paciente.exige_aprovacao = True
    assert servico.exige_aprovacao(cenario.paciente, cenario.profissional)


def test_solicitacao_aprovada_vira_consulta(cenario, fazer_request):
    request = fazer_request(cenario.assistente, cenario.consultorio)
    solicitacao = servico.criar_solicitacao(
        request, paciente=cenario.paciente, profissional=cenario.profissional, horario=amanha(9), tipo="online"
    )
    consulta = servico.aprovar_solicitacao(request, solicitacao)
    assert consulta.inicio == amanha(9) and solicitacao.status == SolicitacaoHorario.Status.APROVADA
    with pytest.raises(ErroAgenda, match="já foi decidida"):
        servico.aprovar_solicitacao(request, solicitacao)


def test_solicitacao_recusada(cenario, fazer_request):
    request = fazer_request(cenario.assistente, cenario.consultorio)
    solicitacao = servico.criar_solicitacao(
        request, paciente=cenario.paciente, profissional=cenario.profissional, horario=amanha(9), tipo="presencial"
    )
    servico.recusar_solicitacao(request, solicitacao)
    assert solicitacao.status == SolicitacaoHorario.Status.RECUSADA and not Consulta.objects.exists()


def test_horarios_livres_seguem_as_regras_e_descontam_consultas(cenario, fazer_request):
    dia = (timezone.now().astimezone(SP) + timedelta(days=7)).date()
    AgendaRegra.objects.create(
        consultorio=cenario.consultorio, profissional=cenario.profissional, dia_semana=dia.weekday(),
        hora_inicio=time(9, 0), hora_fim=time(12, 0), duracao_minutos=60,
    )
    livres = servico.horarios_livres(cenario.consultorio, cenario.profissional, dia)
    assert [h.hour for h in livres] == [9, 10, 11]
    agendar(cenario, fazer_request, inicio=datetime.combine(dia, time(10, 0), tzinfo=SP), duracao_minutos=60)
    assert [h.hour for h in servico.horarios_livres(cenario.consultorio, cenario.profissional, dia)] == [9, 11]


def test_consultas_isoladas_entre_consultorios(cenario, fazer_request, criar_consultorio):
    agendar(cenario, fazer_request)
    outro = criar_consultorio("Clínica B")
    with contexto(consultorio_id=outro.pk):
        assert Consulta.objects.count() == 0
    assert Consulta.objects.count() == 1
