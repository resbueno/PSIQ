import itertools
from datetime import datetime, time, timedelta, timezone as tz
from unittest import mock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.agenda import servico as agenda
from apps.agenda.models import AgendaRegra
from apps.calendarios import provedores, sync
from apps.calendarios.models import ContaCalendario, EventoExterno
from apps.calendarios.provedores import ErroProvedor, Provedor
from apps.contas.models import Perfil
from apps.core.tenancy import contexto, definir_contexto
from conftest import SENHA, entrar_com_2fa

pytestmark = pytest.mark.django_db

SP = ZoneInfo("America/Sao_Paulo")


class ProvedorFalso(Provedor):
    """Calendario externo em memoria, com a mesma interface dos provedores reais."""

    nome = "google"

    def __init__(self):
        self.eventos = {}  # id -> {titulo, inicio, fim}
        self.externos = []  # compromissos pessoais: {id, inicio, fim}
        self.renovacoes = 0
        self.falhar = False
        self._ids = itertools.count(1)

    def configurado(self):
        return True

    def url_autorizacao(self, state, redirect_uri):
        return f"https://provedor.exemplo/auth?state={state}&redirect_uri={redirect_uri}&client_id=abc"

    def trocar_codigo(self, codigo, redirect_uri):
        assert codigo == "codigo-bom"
        return {"acesso": "ACESSO-SECRETO", "refresh": "REFRESH-SECRETO", "expira_em": timezone.now() + timedelta(hours=1), "email": "dra@gmail.com"}

    def renovar(self, refresh):
        self.renovacoes += 1
        return {"acesso": "ACESSO-NOVO", "refresh": "", "expira_em": timezone.now() + timedelta(hours=1)}

    def listar(self, acesso, inicio, fim):
        if self.falhar:
            raise ErroProvedor("HTTP 503")
        proprios = set(self.eventos)
        return [e for e in self.externos if e["id"] not in proprios] + [{"id": i, **{k: v for k, v in e.items() if k in ("inicio", "fim")}} for i, e in self.eventos.items()]

    def criar(self, acesso, titulo, inicio, fim):
        if self.falhar:
            raise ErroProvedor("HTTP 503")
        id_externo = f"evt{next(self._ids)}"
        self.eventos[id_externo] = {"titulo": titulo, "inicio": inicio, "fim": fim}
        return id_externo

    def atualizar(self, acesso, id_externo, titulo, inicio, fim):
        self.eventos[id_externo] = {"titulo": titulo, "inicio": inicio, "fim": fim}

    def apagar(self, acesso, id_externo):
        self.eventos.pop(id_externo, None)


@pytest.fixture
def falso(monkeypatch):
    provedor = ProvedorFalso()
    monkeypatch.setitem(provedores.PROVEDORES, "google", provedor)
    return provedor


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request, falso):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dra. Ana", com_2fa=True)
    c.assistente = criar_usuario("sec@a.com", c.consultorio, Perfil.ASSISTENTE)
    c.prof = c.dra.profissional
    c.maria = criar_paciente(c.consultorio, "Maria da Silva", email="maria@x.com")
    c.req = lambda: fazer_request(c.assistente, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)
    c.conta = ContaCalendario.objects.create(consultorio=c.consultorio, profissional=c.prof, provedor="google", expira_em=timezone.now() + timedelta(hours=1))
    c.conta.guardar_tokens("ACESSO", "REFRESH", timezone.now() + timedelta(hours=1))
    c.conta.save()
    return c


def marcar(cena, dias=3, hora=10):
    inicio = (timezone.now().astimezone(SP) + timedelta(days=dias)).replace(hour=hora, minute=0, second=0, microsecond=0)
    (consulta,) = agenda.agendar(cena.req(), profissional=cena.prof, inicio=inicio, duracao_minutos=60, tipo="presencial", paciente=cena.maria)
    return consulta


# --------------------------------------------------------------------------- exportacao


def test_consulta_vai_ao_calendario_so_com_titulo_neutro(cena, falso):
    consulta = marcar(cena)
    assert sync.sincronizar(cena.conta)
    (evento,) = falso.eventos.values()
    assert evento["titulo"] == "Consulta" and "Maria" not in evento["titulo"]
    assert evento["inicio"] == consulta.inicio
    registro = EventoExterno.objects.get(origem="psiq")
    assert registro.consulta_id == consulta.pk and cena.conta.ultima_sincronizacao and cena.conta.ultimo_erro == ""


def test_nome_do_paciente_so_aparece_se_o_profissional_optar(cena, falso):
    marcar(cena)
    cena.conta.mostra_nome_paciente = True
    cena.conta.save()
    sync.sincronizar(cena.conta)
    assert next(iter(falso.eventos.values()))["titulo"] == "Consulta - Maria da Silva"
    cena.conta.mostra_nome_paciente = False
    cena.conta.save()
    sync.sincronizar(cena.conta)
    assert next(iter(falso.eventos.values()))["titulo"] == "Consulta"  # voltar ao padrao atualiza o evento


def test_remarcar_atualiza_e_cancelar_apaga_o_evento_externo(cena, falso):
    consulta = marcar(cena)
    sync.sincronizar(cena.conta)
    agenda.remarcar(cena.req(), consulta, consulta.inicio + timedelta(hours=2))
    sync.sincronizar(cena.conta)
    assert len(falso.eventos) == 1 and next(iter(falso.eventos.values()))["inicio"] == consulta.inicio
    agenda.cancelar(cena.req(), consulta, "equipe")
    sync.sincronizar(cena.conta)
    assert falso.eventos == {} and not EventoExterno.objects.filter(origem="psiq").exists()


def test_sincronizar_duas_vezes_nao_duplica(cena, falso):
    marcar(cena)
    sync.sincronizar(cena.conta)
    sync.sincronizar(cena.conta)
    assert len(falso.eventos) == 1 and EventoExterno.objects.filter(origem="psiq").count() == 1


# --------------------------------------------------------------------------- importacao e bloqueio


def abrir_agenda(cena):
    for dia in range(7):
        AgendaRegra.objects.create(consultorio=cena.consultorio, profissional=cena.prof, dia_semana=dia, hora_inicio=time(9), hora_fim=time(12), duracao_minutos=60)


def test_compromisso_pessoal_bloqueia_o_horario_oferecido(cena, falso):
    abrir_agenda(cena)
    dia = (timezone.now().astimezone(SP) + timedelta(days=5)).date()
    assert [h.hour for h in agenda.horarios_livres(cena.consultorio, cena.prof, dia)] == [9, 10, 11]

    falso.externos.append({"id": "pessoal1", "inicio": datetime.combine(dia, time(10, 0), tzinfo=SP), "fim": datetime.combine(dia, time(11, 0), tzinfo=SP)})
    assert sync.sincronizar(cena.conta)
    assert EventoExterno.objects.filter(origem="externo").count() == 1
    assert [h.hour for h in agenda.horarios_livres(cena.consultorio, cena.prof, dia)] == [9, 11]

    falso.externos.clear()  # compromisso cancelado la fora: o horario volta
    sync.sincronizar(cena.conta)
    assert [h.hour for h in agenda.horarios_livres(cena.consultorio, cena.prof, dia)] == [9, 10, 11]


def test_o_proprio_evento_exportado_nao_vira_bloqueio(cena, falso):
    abrir_agenda(cena)
    consulta = marcar(cena, dias=4, hora=10)
    sync.sincronizar(cena.conta)
    assert not EventoExterno.objects.filter(origem="externo").exists()
    dia = consulta.inicio.astimezone(SP).date()
    assert 10 not in [h.hour for h in agenda.horarios_livres(cena.consultorio, cena.prof, dia)]  # ocupado pela consulta, nao pelo bloqueio


def test_conta_inativa_nao_bloqueia(cena, falso):
    abrir_agenda(cena)
    dia = (timezone.now().astimezone(SP) + timedelta(days=5)).date()
    falso.externos.append({"id": "p", "inicio": datetime.combine(dia, time(9, 0), tzinfo=SP), "fim": datetime.combine(dia, time(12, 0), tzinfo=SP)})
    sync.sincronizar(cena.conta)
    ContaCalendario.objects.update(ativa=False)
    assert len(agenda.horarios_livres(cena.consultorio, cena.prof, dia)) == 3


# --------------------------------------------------------------------------- tokens e falhas


def test_token_expirando_e_renovado_e_guardado_cifrado(cena, falso):
    cena.conta.expira_em = timezone.now() + timedelta(seconds=10)
    cena.conta.save()
    assert sync.sincronizar(cena.conta)
    assert falso.renovacoes == 1
    cena.conta.refresh_from_db()
    assert cena.conta.token_acesso == "ACESSO-NOVO" and cena.conta.token_refresh == "REFRESH"  # refresh antigo preservado
    assert "ACESSO" not in cena.conta.token_acesso_cifrado and "REFRESH" not in cena.conta.token_refresh_cifrado


def test_falha_do_provedor_fica_registrada_e_nao_levanta_erro(cena, falso):
    marcar(cena)
    falso.falhar = True
    assert sync.sincronizar(cena.conta) is False
    cena.conta.refresh_from_db()
    assert "HTTP 503" in cena.conta.ultimo_erro
    falso.falhar = False
    assert sync.sincronizar(cena.conta) is True
    cena.conta.refresh_from_db()
    assert cena.conta.ultimo_erro == ""


def test_comando_sincroniza_todas_as_contas_ativas(cena, falso):
    marcar(cena)
    call_command("sincronizar_calendarios")
    assert len(falso.eventos) == 1
    ContaCalendario.objects.update(ativa=False)
    marcar(cena, dias=6)
    call_command("sincronizar_calendarios")
    assert len(falso.eventos) == 1


def test_desconectar_apaga_eventos_criados_e_tokens(cena, falso):
    marcar(cena)
    sync.sincronizar(cena.conta)
    sync.desconectar(cena.conta)
    assert falso.eventos == {} and not ContaCalendario.objects.exists() and not EventoExterno.objects.exists()


def test_desconectar_funciona_mesmo_com_provedor_fora(cena, falso):
    marcar(cena)
    sync.sincronizar(cena.conta)
    with mock.patch.object(falso, "apagar", side_effect=ErroProvedor("HTTP 500")):
        sync.desconectar(cena.conta)
    assert not ContaCalendario.objects.exists()


# --------------------------------------------------------------------------- OAuth e telas


def login_prof(client, cena):
    entrar_com_2fa(client, cena.dra)
    definir_contexto(consultorio_id=cena.consultorio.pk)


def test_fluxo_de_conexao_valida_o_state_e_guarda_tokens_cifrados(client, cena, falso):
    ContaCalendario.objects.all().delete()
    login_prof(client, cena)
    resposta = client.get(reverse("calendarios:conectar", args=["google"]))
    assert resposta.status_code == 302 and resposta.url.startswith("https://provedor.exemplo/auth")
    state = parse_qs(urlparse(resposta.url).query)["state"][0]

    ruim = client.get(reverse("calendarios:retorno", args=["google"]), {"code": "codigo-bom", "state": "forjado"})
    assert ruim.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not ContaCalendario.objects.exists()

    client.get(reverse("calendarios:conectar", args=["google"]))  # novo nonce (o anterior foi consumido)
    state = parse_qs(urlparse(client.get(reverse("calendarios:conectar", args=["google"])).url).query)["state"][0]
    ok = client.get(reverse("calendarios:retorno", args=["google"]), {"code": "codigo-bom", "state": state})
    assert ok.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    conta = ContaCalendario.objects.get()
    assert conta.email_conta == "dra@gmail.com" and conta.token_acesso == "ACESSO-SECRETO" and conta.token_refresh == "REFRESH-SECRETO"
    assert "SECRETO" not in conta.token_acesso_cifrado + conta.token_refresh_cifrado
    assert conta.ultima_sincronizacao is not None  # ja sincronizou ao conectar


def test_state_de_outro_usuario_e_recusado(client, cena, falso, criar_usuario):
    ContaCalendario.objects.all().delete()
    outra = criar_usuario("outra@a.com", cena.consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    login_prof(client, cena)
    state_dela = parse_qs(urlparse(client.get(reverse("calendarios:conectar", args=["google"])).url).query)["state"][0]
    client.logout()
    entrar_com_2fa(client, outra)
    client.get(reverse("calendarios:conectar", args=["google"]))
    client.get(reverse("calendarios:retorno", args=["google"]), {"code": "codigo-bom", "state": state_dela})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not ContaCalendario.objects.exists()


def test_provedor_nao_configurado_nao_inicia_oauth(client, cena, monkeypatch):
    monkeypatch.setattr(provedores.PROVEDORES["google"], "configurado", lambda: False)
    login_prof(client, cena)
    resposta = client.get(reverse("calendarios:conectar", args=["google"]), follow=True)
    assert "não foi configurada".encode() in resposta.content
    assert client.get(reverse("calendarios:conectar", args=["inexistente"])).status_code == 404


def test_so_profissional_acessa_e_so_a_propria_conta(client, cena, criar_usuario, falso):
    login_prof(client, cena)
    assert client.get(reverse("calendarios:lista")).status_code == 200
    definir_contexto(consultorio_id=cena.consultorio.pk)
    outra = criar_usuario("outra@a.com", cena.consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    definir_contexto(consultorio_id=cena.consultorio.pk)
    alheia = ContaCalendario.objects.create(consultorio=cena.consultorio, profissional=outra.profissional, provedor="microsoft")
    assert client.post(reverse("calendarios:desconectar", args=[alheia.pk])).status_code == 404
    client.logout()
    client.post(reverse("contas:entrar"), {"email": "sec@a.com", "senha": SENHA})
    assert client.get(reverse("calendarios:lista")).status_code == 403


def test_botoes_da_tela(client, cena, falso):
    marcar(cena)
    login_prof(client, cena)
    client.post(reverse("calendarios:sincronizar", args=[cena.conta.pk]))
    assert len(falso.eventos) == 1
    client.post(reverse("calendarios:alternar_nome", args=[cena.conta.pk]))
    assert next(iter(falso.eventos.values()))["titulo"].startswith("Consulta - ")
    client.post(reverse("calendarios:desconectar", args=[cena.conta.pk]))
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not ContaCalendario.objects.exists() and falso.eventos == {}


# --------------------------------------------------------------------------- clientes REST (requests simulado)


def resposta(json=None, status=200):
    r = mock.Mock(status_code=status)
    r.json.return_value = json or {}
    return r


def test_google_monta_as_chamadas_e_ignora_dia_inteiro_e_cancelados(settings):
    settings.PSIQ_GOOGLE_CLIENT_ID, settings.PSIQ_GOOGLE_CLIENT_SECRET = "id", "segredo"
    google = provedores.Google()
    url = google.url_autorizacao("S", "https://x/retorno")
    consulta = parse_qs(urlparse(url).query)
    assert consulta["access_type"] == ["offline"] and consulta["state"] == ["S"] and "calendar.events" in consulta["scope"][0]

    itens = {"items": [
        {"id": "a", "start": {"dateTime": "2026-03-10T10:00:00-03:00"}, "end": {"dateTime": "2026-03-10T11:00:00-03:00"}},
        {"id": "b", "status": "cancelled", "start": {"dateTime": "2026-03-10T12:00:00Z"}, "end": {"dateTime": "2026-03-10T13:00:00Z"}},
        {"id": "c", "start": {"date": "2026-03-11"}, "end": {"date": "2026-03-12"}},
        {"id": "d", "transparency": "transparent", "start": {"dateTime": "2026-03-10T14:00:00Z"}, "end": {"dateTime": "2026-03-10T15:00:00Z"}},
    ]}
    with mock.patch("apps.calendarios.provedores.requests.get", return_value=resposta(itens)) as get:
        eventos = google.listar("tok", datetime(2026, 3, 1, tzinfo=tz.utc), datetime(2026, 4, 1, tzinfo=tz.utc))
    assert [e["id"] for e in eventos] == ["a"] and eventos[0]["inicio"] == datetime(2026, 3, 10, 13, 0, tzinfo=tz.utc)
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"

    with mock.patch("apps.calendarios.provedores.requests.post", return_value=resposta({"id": "novo"})) as post:
        assert google.criar("tok", "Consulta", datetime(2026, 3, 10, 13, tzinfo=tz.utc), datetime(2026, 3, 10, 14, tzinfo=tz.utc)) == "novo"
    corpo = post.call_args.kwargs["json"]
    assert corpo["summary"] == "Consulta" and "description" not in corpo and "attendees" not in corpo


def test_microsoft_monta_as_chamadas_e_ignora_livre_e_cancelado(settings):
    settings.PSIQ_MICROSOFT_CLIENT_ID, settings.PSIQ_MICROSOFT_CLIENT_SECRET = "id", "segredo"
    ms = provedores.Microsoft()
    assert "offline_access" in parse_qs(urlparse(ms.url_autorizacao("S", "https://x/r")).query)["scope"][0]
    itens = {"value": [
        {"id": "a", "start": {"dateTime": "2026-03-10T13:00:00.0000000"}, "end": {"dateTime": "2026-03-10T14:00:00.0000000"}, "showAs": "busy"},
        {"id": "b", "isCancelled": True, "start": {"dateTime": "2026-03-10T15:00:00.0000000"}, "end": {"dateTime": "2026-03-10T16:00:00.0000000"}},
        {"id": "c", "showAs": "free", "start": {"dateTime": "2026-03-10T17:00:00.0000000"}, "end": {"dateTime": "2026-03-10T18:00:00.0000000"}},
        {"id": "d", "isAllDay": True, "start": {"dateTime": "2026-03-11T00:00:00.0000000"}, "end": {"dateTime": "2026-03-12T00:00:00.0000000"}},
    ]}
    with mock.patch("apps.calendarios.provedores.requests.get", return_value=resposta(itens)):
        eventos = ms.listar("tok", datetime(2026, 3, 1, tzinfo=tz.utc), datetime(2026, 4, 1, tzinfo=tz.utc))
    assert [e["id"] for e in eventos] == ["a"] and eventos[0]["inicio"] == datetime(2026, 3, 10, 13, 0, tzinfo=tz.utc)
    with mock.patch("apps.calendarios.provedores.requests.post", return_value=resposta({"id": "novo"})) as post:
        ms.criar("tok", "Consulta", datetime(2026, 3, 10, 13, tzinfo=tz.utc), datetime(2026, 3, 10, 14, tzinfo=tz.utc))
    assert post.call_args.kwargs["json"]["subject"] == "Consulta"


def test_erro_http_do_provedor_vira_erro_provedor():
    with mock.patch("apps.calendarios.provedores.requests.get", return_value=resposta(status=401)):
        with pytest.raises(ErroProvedor, match="HTTP 401"):
            provedores.Google().listar("tok", datetime(2026, 3, 1, tzinfo=tz.utc), datetime(2026, 4, 1, tzinfo=tz.utc))
