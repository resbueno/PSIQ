from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from unittest import mock

import pytest
from django.core import mail, signing
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.agenda import avisos, servico
from apps.agenda.models import Aviso, Consulta
from apps.contas.models import Perfil
from apps.core.tenancy import contexto, definir_contexto
from apps.pacientes.models import ResponsavelLegal

pytestmark = pytest.mark.django_db

SP = ZoneInfo("America/Sao_Paulo")


def em(dias, hora=10):
    d = timezone.now().astimezone(SP).date() + timedelta(days=dias)
    return datetime.combine(d, time(hora, 0), tzinfo=SP)


@pytest.fixture
def cenario(criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    consultorio = criar_consultorio("Clínica Aurora")
    doutora = criar_usuario("dra@a.com", consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    assistente = criar_usuario("sec@a.com", consultorio, Perfil.ASSISTENTE)
    paciente = criar_paciente(consultorio, "Maria da Silva", email="maria@x.com")

    class C:
        pass

    c = C()
    c.consultorio, c.profissional, c.paciente = consultorio, doutora.profissional, paciente
    c.request = fazer_request(assistente, consultorio)
    definir_contexto(consultorio_id=consultorio.pk)

    def agendar(inicio=None, tipo="presencial", **extra):
        (consulta,) = servico.agendar(
            c.request, profissional=c.profissional, inicio=inicio or em(3), duracao_minutos=50, tipo=tipo,
            paciente=extra.pop("paciente", c.paciente), **extra,
        )
        return consulta

    c.agendar = agendar
    return c


def test_confirmacao_vai_por_email_com_texto_neutro(cenario):
    cenario.agendar(tipo="presencial")
    assert len(mail.outbox) == 1
    mensagem = mail.outbox[0]
    assert mensagem.to == ["maria@x.com"] and "Clínica Aurora" in mensagem.subject
    corpo = mensagem.body.lower()
    assert "compromisso" in corpo
    for proibida in ("online", "presencial", "consulta", "psic", "psiquiatr", "terapia"):
        assert proibida not in corpo
    aviso = Aviso.objects.get()
    assert aviso.status == Aviso.Status.ENVIADO and aviso.tipo == Aviso.Tipo.CONFIRMACAO


def test_consulta_online_leva_o_link_da_sala(cenario):
    consulta = cenario.agendar(tipo="online")
    assert consulta.link_online in mail.outbox[0].body
    assert "online" not in mail.outbox[0].body.lower().replace(consulta.link_online.lower(), "")


def test_horario_no_aviso_usa_o_fuso_do_consultorio(cenario):
    cenario.agendar(inicio=em(3, hora=14))
    assert "às 14:00" in mail.outbox[0].body


def test_menor_recebe_apenas_via_responsavel(cenario, criar_paciente):
    crianca = criar_paciente(cenario.consultorio, "Joãozinho", email="crianca@x.com",
                             nascimento=timezone.now().date().replace(year=timezone.now().year - 9))
    ResponsavelLegal.objects.create(consultorio=cenario.consultorio, paciente=crianca, nome="Mãe do João", email="mae@x.com")
    ResponsavelLegal.objects.create(consultorio=cenario.consultorio, paciente=crianca, nome="Pai", email="pai@x.com", recebe_avisos=False)
    cenario.agendar(paciente=crianca)
    assert [m.to for m in mail.outbox] == [["mae@x.com"]]


def test_falha_do_canal_e_registrada_e_nao_quebra_o_agendamento(cenario):
    with mock.patch.object(avisos.CanalEmail, "enviar", side_effect=ConnectionError("smtp fora")):
        consulta = cenario.agendar()
    aviso = consulta.avisos.get()
    assert aviso.status == Aviso.Status.FALHOU and aviso.erro == "ConnectionError"
    assert "smtp" not in aviso.erro


def test_paciente_sem_email_nao_gera_aviso(cenario, criar_paciente):
    sem = criar_paciente(cenario.consultorio, "Sem Email")
    consulta = cenario.agendar(paciente=sem)
    assert consulta.avisos.count() == 0 and not mail.outbox


def test_cancelamento_e_remarcacao_geram_avisos(cenario):
    consulta = cenario.agendar()
    servico.remarcar(cenario.request, consulta, em(4))
    servico.cancelar(cenario.request, consulta, Consulta.CanceladoPor.EQUIPE)
    tipos = list(consulta.avisos.order_by("criado_em").values_list("tipo", flat=True))
    assert tipos == ["confirmacao", "remarcacao", "cancelamento"]
    assert "cancelado" in mail.outbox[-1].body and "link" not in mail.outbox[-1].body.lower()
    assert "/c/" not in mail.outbox[-1].body  # cancelamento nao traz link de acao


# --- link publico (sem login) ---


def link(cenario, consulta):
    return reverse("agenda:acao_publica", args=[avisos.token_acao(consulta, cenario.paciente)])


def test_token_so_vale_com_assinatura_correta(cenario):
    consulta = cenario.agendar()
    token = avisos.token_acao(consulta, cenario.paciente)
    assert avisos.ler_token_acao(token)["c"] == str(consulta.pk)
    with pytest.raises(signing.BadSignature):
        avisos.ler_token_acao(token[:-2] + "xx")


def test_link_publico_mostra_so_data_e_confirma(client, cenario):
    consulta = cenario.agendar(inicio=em(3, hora=15))
    url = link(cenario, consulta)
    definir_contexto(None, None)
    pagina = client.get(url)
    assert pagina.status_code == 200
    assert b"15:00" in pagina.content and b"Maria" not in pagina.content  # nome do paciente nao aparece
    assert client.get(url).status_code == 200  # GET (ex.: leitor de e-mail) nao altera nada
    definir_contexto(consultorio_id=cenario.consultorio.pk)
    assert Consulta.objects.get(pk=consulta.pk).status == Consulta.Status.AGENDADA

    resposta = client.post(url, {"acao": "confirmar"})
    assert resposta.status_code == 200
    definir_contexto(consultorio_id=cenario.consultorio.pk)
    assert Consulta.objects.get(pk=consulta.pk).status == Consulta.Status.CONFIRMADA


def test_link_publico_cancela_dentro_do_prazo(client, cenario):
    consulta = cenario.agendar(inicio=em(3))
    client.post(link(cenario, consulta), {"acao": "cancelar"})
    definir_contexto(consultorio_id=cenario.consultorio.pk)
    consulta.refresh_from_db()
    assert consulta.status == Consulta.Status.CANCELADA and consulta.cancelada_por == "paciente"


def test_link_publico_nao_cancela_fora_do_prazo(client, cenario):
    (perto,) = servico.agendar(
        cenario.request, profissional=cenario.profissional, inicio=timezone.now() + timedelta(hours=3),
        duracao_minutos=50, tipo="presencial", paciente=cenario.paciente,
    )
    resposta = client.post(link(cenario, perto), {"acao": "cancelar"})
    assert b"24 horas" in resposta.content
    definir_contexto(consultorio_id=cenario.consultorio.pk)
    perto.refresh_from_db()
    assert perto.ativa


def test_link_invalido_responde_404(client, db):
    assert client.get(reverse("agenda:acao_publica", args=["token-falso"])).status_code == 404


def test_token_forjado_e_rejeitado(client, cenario, criar_consultorio):
    """Token assinado com outro sal (forjado) e rejeitado."""
    consulta = cenario.agendar()
    forjado = signing.dumps(
        {"c": str(consulta.pk), "k": str(criar_consultorio("Clínica B").pk), "p": str(cenario.paciente.pk)},
        salt="outro-sal",
    )
    assert client.get(reverse("agenda:acao_publica", args=[forjado])).status_code == 404


# --- lembretes ---


def test_lembrete_e_enviado_uma_unica_vez(cenario):
    cenario.agendar(inicio=timezone.now() + timedelta(hours=20))
    cenario.agendar(inicio=em(10))  # fora da janela de 24 h
    mail.outbox.clear()
    call_command("enviar_lembretes")
    assert len(mail.outbox) == 1 and "Lembrete" in mail.outbox[0].body
    call_command("enviar_lembretes")
    assert len(mail.outbox) == 1
