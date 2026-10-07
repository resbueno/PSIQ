import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.agenda import servico as agenda
from apps.agenda.models import AgendaRegra, Consulta, SolicitacaoHorario
from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil
from apps.core.tenancy import contexto, definir_contexto
from apps.financeiro.models import Lancamento, Recibo
from apps.financeiro import servico as financeiro
from apps.pacientes.models import ResponsavelLegal
from apps.portal import servico
from apps.portal.models import CodigoAcesso, SolicitacaoLGPD
from apps.prontuario import servico as prontuario
from conftest import SENHA

pytestmark = pytest.mark.django_db

SP = ZoneInfo("America/Sao_Paulo")


def anos_atras(n):
    hoje = timezone.now().date()
    return hoje.replace(year=hoje.year - n)


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dra. Ana", com_2fa=True)
    c.assistente = criar_usuario("sec@a.com", c.consultorio, Perfil.ASSISTENTE, nome="Sec")
    c.prof = c.dra.profissional
    c.maria = criar_paciente(c.consultorio, "Maria da Silva", email="maria@x.com", cpf="52998224725", nascimento=anos_atras(35))
    c.req = lambda u=None: fazer_request(u or c.assistente, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)
    # agenda aberta todos os dias, 9h-12h, sessoes de 60 min
    for dia in range(7):
        AgendaRegra.objects.create(consultorio=c.consultorio, profissional=c.prof, dia_semana=dia, hora_inicio=time(9), hora_fim=time(12), duracao_minutos=60)
    return c


def entrar_no_portal(client, cena, email="maria@x.com"):
    mail.outbox.clear()
    client.post(reverse("portal:entrar", args=[cena.consultorio.pk]), {"email": email})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    codigo = re.search(r"código de acesso é (\d{6})", mail.outbox[-1].body).group(1)
    resposta = client.post(reverse("portal:codigo", args=[cena.consultorio.pk]), {"codigo": codigo})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    return resposta


# --------------------------------------------------------------------------- acesso por codigo


def test_codigo_vai_por_email_e_so_o_hash_fica_guardado(client, cena):
    mail.outbox.clear()
    resposta = client.post(reverse("portal:entrar", args=[cena.consultorio.pk]), {"email": "MARIA@x.com"})
    assert resposta.status_code == 302
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["maria@x.com"]
    codigo = re.search(r"(\d{6})", mail.outbox[0].body).group(1)
    definir_contexto(consultorio_id=cena.consultorio.pk)
    registro = CodigoAcesso.objects.get()
    assert codigo not in registro.codigo_hash and len(registro.codigo_hash) == 64


def test_email_desconhecido_recebe_a_mesma_resposta_e_nada_e_enviado(client, cena):
    mail.outbox.clear()
    resposta = client.post(reverse("portal:entrar", args=[cena.consultorio.pk]), {"email": "ninguem@x.com"}, follow=True)
    assert not mail.outbox and "Se o e-mail estiver cadastrado".encode() in resposta.content
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not CodigoAcesso.objects.exists()


def test_login_completo_e_sem_sessao_e_barrado(client, cena):
    assert client.get(reverse("portal:home")).status_code == 401
    assert entrar_no_portal(client, cena).status_code == 302
    pagina = client.get(reverse("portal:home"))
    assert pagina.status_code == 200 and "Olá, Maria".encode() in pagina.content and "no-store" in pagina["Cache-Control"]
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Auditoria.objects.filter(acao="portal_login").exists()
    client.post(reverse("portal:sair"))
    assert client.get(reverse("portal:home")).status_code == 401


def test_codigo_errado_gasta_tentativas_e_depois_nem_o_certo_vale(client, cena):
    mail.outbox.clear()
    client.post(reverse("portal:entrar", args=[cena.consultorio.pk]), {"email": "maria@x.com"})
    certo = re.search(r"(\d{6})", mail.outbox[0].body).group(1)
    errado = "000000" if certo != "000000" else "111111"
    for _ in range(5):
        assert "incorreto".encode() in client.post(reverse("portal:codigo", args=[cena.consultorio.pk]), {"codigo": errado}).content
    resposta = client.post(reverse("portal:codigo", args=[cena.consultorio.pk]), {"codigo": certo})
    assert resposta.status_code == 200  # recusado
    assert client.get(reverse("portal:home")).status_code == 401


def test_codigo_vale_uma_vez_e_expira(client, cena):
    mail.outbox.clear()
    client.post(reverse("portal:entrar", args=[cena.consultorio.pk]), {"email": "maria@x.com"})
    codigo = re.search(r"(\d{6})", mail.outbox[0].body).group(1)
    definir_contexto(consultorio_id=cena.consultorio.pk)
    CodigoAcesso.objects.update(expira_em=timezone.now() - timedelta(seconds=1))
    assert client.post(reverse("portal:codigo", args=[cena.consultorio.pk]), {"codigo": codigo}).status_code == 200
    assert client.get(reverse("portal:home")).status_code == 401


def test_limite_de_codigos_por_hora(cena, fazer_request):
    request = cena.req()
    enviados = [servico.solicitar_codigo(request, cena.consultorio, "maria@x.com") for _ in range(7)]
    assert enviados == [True] * 5 + [False] * 2


def test_codigo_de_um_consultorio_nao_abre_outro(client, cena, criar_consultorio):
    outro = criar_consultorio("Outra Clínica")
    mail.outbox.clear()
    client.post(reverse("portal:entrar", args=[outro.pk]), {"email": "maria@x.com"})
    assert not mail.outbox  # Maria nao e paciente da outra clinica


# --------------------------------------------------------------------------- menores, responsaveis e varios pacientes


def test_menor_de_16_so_pelo_responsavel(client, cena, criar_paciente):
    crianca = criar_paciente(cena.consultorio, "Joãozinho", email="joao@x.com", nascimento=anos_atras(10))
    ResponsavelLegal.objects.create(consultorio=cena.consultorio, paciente=crianca, nome="Mãe", email="mae@x.com")
    mail.outbox.clear()
    client.post(reverse("portal:entrar", args=[cena.consultorio.pk]), {"email": "joao@x.com"})
    assert not mail.outbox  # acesso proprio so a partir de 16 anos

    entrar_no_portal(client, cena, "mae@x.com")
    assert "Joãozinho".encode() in client.get(reverse("portal:home")).content


def test_adolescente_de_16_anos_tem_acesso_proprio(client, cena, criar_paciente):
    criar_paciente(cena.consultorio, "Teen", email="teen@x.com", nascimento=anos_atras(16))
    assert entrar_no_portal(client, cena, "teen@x.com").status_code == 302


def test_responsavel_de_dois_filhos_alterna_entre_eles(client, cena, criar_paciente):
    for nome in ("Ana Filha", "Bia Filha"):
        filha = criar_paciente(cena.consultorio, nome, nascimento=anos_atras(8))
        ResponsavelLegal.objects.create(consultorio=cena.consultorio, paciente=filha, nome="Mãe", email="mae@x.com")
    entrar_no_portal(client, cena, "mae@x.com")
    assert "Ana Filha".encode() in client.get(reverse("portal:home")).content
    definir_contexto(consultorio_id=cena.consultorio.pk)
    from apps.pacientes.models import Paciente

    bia = Paciente.objects.get(nome="Bia Filha")
    client.post(reverse("portal:trocar_paciente"), {"paciente": str(bia.pk)})
    assert "Olá, Bia".encode() in client.get(reverse("portal:home")).content
    maria = cena.maria
    client.post(reverse("portal:trocar_paciente"), {"paciente": str(maria.pk)})  # nao e dela: ignorado
    assert "Olá, Bia".encode() in client.get(reverse("portal:home")).content


# --------------------------------------------------------------------------- consultas e agendamento


def consulta_para(cena, paciente, inicio):
    (c,) = agenda.agendar(cena.req(), profissional=cena.prof, inicio=inicio, duracao_minutos=60, tipo="online", paciente=paciente)
    return c


def test_paciente_ve_confirma_e_cancela_a_propria_consulta(client, cena):
    consulta = consulta_para(cena, cena.maria, timezone.now() + timedelta(days=3))
    entrar_no_portal(client, cena)
    assert b"Confirmar" in client.get(reverse("portal:home")).content
    client.post(reverse("portal:consulta_acao", args=[consulta.pk]), {"acao": "confirmar"})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Consulta.objects.get(pk=consulta.pk).status == "confirmada"
    client.post(reverse("portal:consulta_acao", args=[consulta.pk]), {"acao": "cancelar"})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Consulta.objects.get(pk=consulta.pk).status == "cancelada"


def test_cancelamento_pelo_portal_respeita_o_prazo(client, cena):
    consulta = consulta_para(cena, cena.maria, timezone.now() + timedelta(hours=3))
    entrar_no_portal(client, cena)
    resposta = client.post(reverse("portal:consulta_acao", args=[consulta.pk]), {"acao": "cancelar"}, follow=True)
    assert "24 horas".encode() in resposta.content
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Consulta.objects.get(pk=consulta.pk).ativa


def test_nao_mexe_na_consulta_de_outro_paciente(client, cena, criar_paciente):
    outro = criar_paciente(cena.consultorio, "Outro Paciente", email="outro@x.com")
    alheia = consulta_para(cena, outro, timezone.now() + timedelta(days=3))
    entrar_no_portal(client, cena)
    assert client.post(reverse("portal:consulta_acao", args=[alheia.pk]), {"acao": "cancelar"}).status_code == 404
    assert b"Outro Paciente" not in client.get(reverse("portal:home")).content


def test_sala_online_so_abre_perto_do_horario(client, cena):
    consulta_para(cena, cena.maria, timezone.now() + timedelta(days=3))
    entrar_no_portal(client, cena)
    assert b"meet.jit.si" not in client.get(reverse("portal:home")).content
    definir_contexto(consultorio_id=cena.consultorio.pk)
    Consulta.objects.update(inicio=timezone.now() + timedelta(minutes=10), fim=timezone.now() + timedelta(minutes=70))
    assert b"meet.jit.si" in client.get(reverse("portal:home")).content


def primeiro_horario(client, cena):
    pagina = client.get(reverse("portal:agendar")).content.decode()
    return re.search(r'name="inicio" value="([^"]+)"', pagina).group(1)


def test_primeiro_horario_vira_pedido_e_retorno_marca_na_hora(client, cena):
    entrar_no_portal(client, cena)
    pagina = client.get(reverse("portal:agendar"))
    assert "será um <strong>pedido</strong>".encode() in pagina.content
    inicio = primeiro_horario(client, cena)
    client.post(reverse("portal:agendar"), {"profissional": str(cena.prof.pk), "inicio": inicio, "tipo": "presencial"})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    solicitacao = SolicitacaoHorario.objects.get()
    assert solicitacao.status == "pendente" and not Consulta.objects.exists()

    agenda.aprovar_solicitacao(cena.req(), solicitacao)  # a equipe aprova: agora ha historico
    mail.outbox.clear()
    pagina = client.get(reverse("portal:agendar"))
    assert "Ele é marcado na hora".encode() in pagina.content
    novo_inicio = primeiro_horario(client, cena)
    resposta = client.post(reverse("portal:agendar"), {"profissional": str(cena.prof.pk), "inicio": novo_inicio, "tipo": "online"})
    assert resposta.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Consulta.objects.count() == 2 and len(mail.outbox) == 1
    assert novo_inicio not in client.get(reverse("portal:agendar")).content.decode()  # horario ocupado some


def test_horario_fora_da_agenda_e_recusado(client, cena):
    entrar_no_portal(client, cena)
    madrugada = (timezone.now().astimezone(SP) + timedelta(days=2)).replace(hour=3, minute=0, second=0, microsecond=0)
    resposta = client.post(reverse("portal:agendar"), {"profissional": str(cena.prof.pk), "inicio": madrugada.isoformat(), "tipo": "presencial"}, follow=True)
    assert "não está mais disponível".encode() in resposta.content
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not Consulta.objects.exists() and not SolicitacaoHorario.objects.exists()


# --------------------------------------------------------------------------- pagamentos e documentos


def test_pagamentos_e_recibos_so_do_proprio_paciente(client, cena, criar_paciente):
    outra = criar_paciente(cena.consultorio, "Outra Pessoa", email="outra@x.com", cpf="11144477735")
    proprio = Lancamento.objects.create(consultorio=cena.consultorio, paciente=cena.maria, profissional=cena.prof, tipo="particular", origem="manual", valor=100, vencimento=date.today())
    alheio = Lancamento.objects.create(consultorio=cena.consultorio, paciente=outra, profissional=cena.prof, tipo="particular", origem="manual", valor=777, vencimento=date.today())
    recibos = {}
    for lancamento in (proprio, alheio):
        financeiro.registrar_pagamento(cena.req(), lancamento, forma="pix")
        lancamento = Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=lancamento.pk)
        recibos[lancamento.pk] = financeiro.emitir_recibo(cena.req(), lancamento)
    entrar_no_portal(client, cena)
    pagina = client.get(reverse("portal:pagamentos")).content
    assert b"100" in pagina and b"777" not in pagina
    assert client.get(reverse("portal:recibo", args=[recibos[proprio.pk].pk])).content.startswith(b"%PDF")
    assert client.get(reverse("portal:recibo", args=[recibos[alheio.pk].pk])).status_code == 404


def test_documentos_so_os_liberados_ou_sem_conteudo_clinico(client, cena):
    pront = prontuario.abrir_prontuario(cena.req(cena.dra), cena.prof, paciente=cena.maria)
    escondido = prontuario.emitir_documento_clinico(cena.req(cena.dra), pront, titulo="Relatório reservado", texto="Texto sigiloso.")
    liberado = prontuario.emitir_documento_clinico(cena.req(cena.dra), pront, titulo="Atestado liberado", texto="Atesto.")
    liberado.liberado_ao_paciente = True
    liberado.save()
    entrar_no_portal(client, cena)
    pagina = client.get(reverse("portal:documentos")).content
    assert b"Atestado liberado" in pagina and "Relatório reservado".encode() not in pagina
    assert client.get(reverse("portal:documento", args=[escondido.pk])).status_code == 404
    baixado = client.get(reverse("portal:documento", args=[liberado.pk]))
    assert baixado.content.startswith(b"%PDF")
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Auditoria.objects.filter(acao="documento_baixado_pelo_paciente").exists()


# --------------------------------------------------------------------------- privacidade


def test_paciente_ve_quem_abriu_o_prontuario_e_revoga_autorizacao(client, cena, criar_usuario):
    outro = criar_usuario("dr@a.com", cena.consultorio, Perfil.PROFISSIONAL, nome="Dr. Beto", com_2fa=True)
    pront = prontuario.abrir_prontuario(cena.req(cena.dra), cena.prof, paciente=cena.maria)
    from apps.auditoria import servico as auditoria

    auditoria.registrar(cena.req(cena.dra), "prontuario_aberto", "prontuario", pront.pk)
    (consentimento,) = prontuario.solicitar_consentimento(cena.req(cena.dra), pront, outro.profissional)
    prontuario.aceitar_consentimento(consentimento, "198.51.100.5")

    entrar_no_portal(client, cena)
    pagina = client.get(reverse("portal:privacidade")).content.decode()
    assert "Dra. Ana" in pagina and "Dr. Beto pode ler o prontuário de Dra. Ana" in pagina
    client.post(reverse("portal:revogar_consentimento", args=[consentimento.pk]))
    definir_contexto(consultorio_id=cena.consultorio.pk)
    consentimento.refresh_from_db()
    assert consentimento.revogado_em is not None


def test_pedido_do_titular_chega_a_equipe(client, cena):
    entrar_no_portal(client, cena)
    client.post(reverse("portal:privacidade"), {"tipo": "copia", "mensagem": "Quero uma cópia."})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    pedido = SolicitacaoLGPD.objects.get()
    assert pedido.tipo == "copia" and pedido.status == "aberta"
    client.post(reverse("portal:sair"))

    client.post(reverse("contas:entrar"), {"email": "sec@a.com", "senha": SENHA})
    assert "Maria da Silva".encode() in client.get(reverse("portal:lgpd_lista")).content
    client.post(reverse("portal:lgpd_atender", args=[pedido.pk]))
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert SolicitacaoLGPD.objects.get().status == "atendida"


def test_aviso_de_consulta_traz_o_link_do_portal(cena):
    mail.outbox.clear()
    consulta_para(cena, cena.maria, timezone.now() + timedelta(days=3))
    assert f"/portal/{cena.consultorio.pk}/entrar/" in mail.outbox[0].body


# --------------------------------------------------------------------------- agendamento publico (link /<slug>/agenda/)


def primeiro_horario_publico(client, cena):
    pagina = client.get(reverse("publico_agenda", args=[cena.consultorio.slug])).content.decode()
    return re.search(r'<option value="([^"]+)">', pagina[pagina.index('id="inicio"'):]).group(1)


def dados_pre_cadastro(**extra):
    return {"nome": "Novo Paciente", "email": "novo@fora.com", "telefone": "11999990000", "nascimento": "", "site": "", **extra}


def test_pagina_publica_mostra_horarios_sem_login(client, cena):
    resposta = client.get(reverse("publico_agenda", args=[cena.consultorio.slug]))
    assert resposta.status_code == 200
    assert cena.consultorio.nome.encode() in resposta.content


def test_agendamento_publico_cria_pre_cadastro_e_sempre_vira_pedido(client, cena):
    inicio = primeiro_horario_publico(client, cena)
    resposta = client.post(
        reverse("publico_agenda", args=[cena.consultorio.slug]),
        {"profissional": str(cena.prof.pk), "inicio": inicio, "tipo": "presencial", **dados_pre_cadastro()},
        follow=True,
    )
    assert resposta.status_code == 200
    assert "Pedido enviado".encode() in resposta.content

    definir_contexto(consultorio_id=cena.consultorio.pk)
    from apps.pacientes.models import Paciente

    paciente = Paciente.objects.get(email="novo@fora.com")
    assert paciente.nome == "Novo Paciente"
    solicitacao = SolicitacaoHorario.objects.get(paciente=paciente)
    assert solicitacao.status == "pendente" and not Consulta.objects.filter(paciente=paciente).exists()


def test_agendamento_publico_reaproveita_paciente_existente_pelo_email(client, cena):
    inicio = primeiro_horario_publico(client, cena)
    client.post(
        reverse("publico_agenda", args=[cena.consultorio.slug]),
        {"profissional": str(cena.prof.pk), "inicio": inicio, "tipo": "presencial", **dados_pre_cadastro(email="maria@x.com", nome="Maria da Silva")},
    )
    definir_contexto(consultorio_id=cena.consultorio.pk)
    from apps.pacientes.models import Paciente

    assert Paciente.objects.filter(email="maria@x.com").count() == 1
    assert SolicitacaoHorario.objects.filter(paciente=cena.maria).exists()


def test_agendamento_publico_rejeita_robo_pelo_campo_isca(client, cena):
    inicio = primeiro_horario_publico(client, cena)
    client.post(
        reverse("publico_agenda", args=[cena.consultorio.slug]),
        {"profissional": str(cena.prof.pk), "inicio": inicio, "tipo": "presencial", **dados_pre_cadastro(site="http://spam.com")},
    )
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not SolicitacaoHorario.objects.exists()


def test_agendamento_publico_limita_pedidos_repetidos_do_mesmo_email(client, cena):
    for _ in range(4):
        inicio = primeiro_horario_publico(client, cena)
        client.post(
            reverse("publico_agenda", args=[cena.consultorio.slug]),
            {"profissional": str(cena.prof.pk), "inicio": inicio, "tipo": "presencial", **dados_pre_cadastro()},
        )
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert SolicitacaoHorario.objects.count() == 3  # o 4o pedido esbarra no limite por hora
