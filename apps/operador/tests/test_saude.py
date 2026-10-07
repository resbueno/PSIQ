import os
import time
import uuid
from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.agenda import servico as agenda
from apps.agenda.models import Aviso
from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil
from apps.core.tenancy import definir_contexto
from apps.operador.management.commands.verificar_saude import verificar
from apps.relatorios.models import Exportacao

pytestmark = pytest.mark.django_db


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request, settings, tmp_path):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    c.maria = criar_paciente(c.consultorio, "Maria", email="m@x.com")
    c.req = lambda: fazer_request(c.dra, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)
    c.marcador = tmp_path / "ultimo-backup-ok"
    c.marcador.write_text("ok")
    settings.PSIQ_BACKUP_MARCADOR = str(c.marcador)
    return c


def consulta(cena):
    (x,) = agenda.agendar(cena.req(), profissional=cena.dra.profissional, inicio=timezone.now() + timedelta(days=3), duracao_minutos=50, tipo="presencial", paciente=cena.maria)
    return x


def test_endpoint_de_saude_responde_sem_login(client, db):
    resposta = client.get(reverse("saude"))
    assert resposta.status_code == 200 and resposta.json() == {"ok": True}


def test_tudo_certo_nao_gera_alerta(cena):
    assert verificar() == []


def test_backup_ausente_ou_antigo_gera_alerta(cena, settings):
    antigo = time.time() - 30 * 3600
    os.utime(cena.marcador, (antigo, antigo))
    assert any("Backup" in a and "30 h" in a for a in verificar())
    os.remove(cena.marcador)
    assert any("não existe" in a for a in verificar())
    settings.PSIQ_BACKUP_MARCADOR = ""
    assert any("não configurado" in a for a in verificar())


def test_contas_com_muitas_falhas_de_login(cena, settings):
    cena.dra.falhas_login = settings.PSIQ_ALERTA_LOGINS_FALHOS
    cena.dra.save()
    assert any("falhas de login" in a for a in verificar())


def test_falhas_de_aviso_acima_do_limite(cena, settings):
    x = consulta(cena)
    for _ in range(settings.PSIQ_ALERTA_FALHAS_DE_AVISO):
        Aviso.objects.create(consultorio=cena.consultorio, consulta=x, canal="email", tipo="lembrete", destinatario="a@a.com", status="falhou")
    alertas = verificar()
    assert any("envios falharam" in a and "Clínica Aurora" in a for a in alertas)


def test_exportacoes_em_massa(cena, settings):
    for _ in range(settings.PSIQ_ALERTA_EXPORTACOES):
        Exportacao.objects.create(consultorio=cena.consultorio, solicitada_por_id=cena.dra.pk, tipo="consultorio")
    assert any("exportações" in a for a in verificar())


def test_suporte_fora_de_autorizacao_vigente(cena):
    Auditoria.objects.create(consultorio_id=cena.consultorio.pk, acao="suporte_requisicao", objeto="suporte", objeto_id=str(uuid.uuid4()))
    assert any("fora de uma autorização vigente" in a for a in verificar())


def test_comando_envia_email_ao_operador_so_quando_ha_alerta(cena, settings):
    settings.PSIQ_ALERTA_EMAIL = "operador@psiq.com"
    mail.outbox.clear()
    call_command("verificar_saude")
    assert not mail.outbox
    os.remove(cena.marcador)
    call_command("verificar_saude")
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["operador@psiq.com"] and "Backup" in mail.outbox[0].body
