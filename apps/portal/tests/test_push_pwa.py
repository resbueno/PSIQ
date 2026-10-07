import json
from datetime import time, timedelta
from unittest import mock

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from pywebpush import WebPushException

from apps.agenda import avisos, servico as agenda
from apps.agenda.models import Aviso
from apps.contas.models import Perfil
from apps.core.tenancy import definir_contexto
from apps.portal.models import AssinaturaPush
from apps.portal.tests.test_portal import entrar_no_portal

pytestmark = pytest.mark.django_db

ENDPOINT = "https://push.exemplo.com/abc123"


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request, settings):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    c.assistente = criar_usuario("sec@a.com", c.consultorio, Perfil.ASSISTENTE)
    c.maria = criar_paciente(c.consultorio, "Maria da Silva", email="maria@x.com")
    c.req = lambda: fazer_request(c.assistente, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)
    settings.MEUPSIQ_VAPID_PUBLIC_KEY, settings.MEUPSIQ_VAPID_PRIVATE_KEY = "publica", "privada"
    return c


def inscrever(cena, endpoint=ENDPOINT):
    return AssinaturaPush.objects.create(
        consultorio=cena.consultorio, paciente=cena.maria, endpoint=endpoint, p256dh="p256", auth="auth"
    )


def agendar(cena):
    (consulta,) = agenda.agendar(
        cena.req(), profissional=cena.dra.profissional, inicio=timezone.now() + timedelta(days=3), duracao_minutos=50,
        tipo="online", paciente=cena.maria,
    )
    return consulta


def test_manifesto_e_service_worker_sao_servidos_na_raiz(client, db):
    manifesto = client.get("/manifest.webmanifest")
    assert manifesto.status_code == 200 and manifesto["Content-Type"].startswith("application/manifest+json")
    assert json.loads(manifesto.content)["display"] == "standalone"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw["Service-Worker-Allowed"] == "/" and "javascript" in sw["Content-Type"]
    corpo = sw.content.decode()
    assert "addEventListener('push'" in corpo and "caches." not in corpo and "'fetch'" not in corpo  # nada de cache offline


def test_portal_registra_o_aparelho_e_valida_o_corpo(client, cena, criar_paciente):
    entrar_no_portal(client, cena)
    corpo = {"endpoint": ENDPOINT, "keys": {"p256dh": "chave", "auth": "segredo"}}
    ok = client.post(reverse("portal:push_inscrever"), json.dumps(corpo), content_type="application/json")
    assert ok.status_code == 200
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert AssinaturaPush.objects.get().ativa
    for ruim in ({}, {"endpoint": "http://inseguro", "keys": {"p256dh": "a", "auth": "b"}}, {"endpoint": ENDPOINT}):
        assert client.post(reverse("portal:push_inscrever"), json.dumps(ruim), content_type="application/json").status_code == 400
    assert client.post(reverse("portal:push_inscrever"), "isto nao e json", content_type="application/json").status_code == 400
    client.post(reverse("portal:push_cancelar"), json.dumps({"endpoint": ENDPOINT}), content_type="application/json")
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not AssinaturaPush.objects.get().ativa


def test_push_exige_sessao_do_portal(client, db):
    assert client.post(reverse("portal:push_inscrever"), "{}", content_type="application/json").status_code == 401


def test_aviso_vai_por_email_e_por_push_em_cada_aparelho(cena):
    inscrever(cena, ENDPOINT)
    inscrever(cena, "https://push.exemplo.com/outro-aparelho")
    mail.outbox.clear()
    with mock.patch("apps.portal.push.webpush") as enviar:
        consulta = agendar(cena)
    assert len(mail.outbox) == 1 and enviar.call_count == 2
    registros = Aviso.objects.filter(consulta=consulta)
    assert sorted(registros.values_list("canal", flat=True)) == ["email", "push", "push"]
    assert all(a.status == "enviado" for a in registros)

    carga = json.loads(enviar.call_args.kwargs["data"])
    assert carga["title"] == "Aviso de Clínica Aurora"
    texto = (carga["title"] + carga["body"]).lower()
    assert "compromisso" in texto and "/portal/" in carga["url"]
    for proibida in ("online", "presencial", "psic", "terapia", "consulta"):
        assert proibida not in texto


def test_sem_chaves_vapid_nao_ha_push(cena, settings):
    inscrever(cena)
    settings.MEUPSIQ_VAPID_PRIVATE_KEY = ""
    with mock.patch("apps.portal.push.webpush") as enviar:
        agendar(cena)
    assert not enviar.called and not Aviso.objects.filter(canal="push").exists()


def test_aparelho_que_saiu_e_desativado_e_a_falha_fica_registrada(cena):
    assinatura = inscrever(cena)
    resposta = mock.Mock(status_code=410)
    with mock.patch("apps.portal.push.webpush", side_effect=WebPushException("gone", response=resposta)):
        consulta = agendar(cena)
    aviso = Aviso.objects.get(consulta=consulta, canal="push")
    assert aviso.status == "falhou" and aviso.erro == "WebPushException"
    assert not AssinaturaPush.objects.get(pk=assinatura.pk).ativa
    assert Aviso.objects.get(consulta=consulta, canal="email").status == "enviado"  # o e-mail nao e afetado


def test_assinatura_inativa_nao_recebe(cena):
    assinatura = inscrever(cena)
    AssinaturaPush.objects.filter(pk=assinatura.pk).update(ativa=False)
    with mock.patch("apps.portal.push.webpush") as enviar:
        agendar(cena)
    assert not enviar.called


def test_canais_registrados():
    assert set(avisos.CANAIS) == {"email", "push"}
