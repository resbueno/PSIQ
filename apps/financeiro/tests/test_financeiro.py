from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.agenda import servico as agenda
from apps.agenda.models import Consulta
from apps.contas.models import Perfil
from apps.core import cripto
from apps.core.tenancy import contexto, definir_contexto
from apps.financeiro import servico
from apps.financeiro.models import (
    AtendimentoConvenio, CobrancaRecorrente, Convenio, Lancamento, Recibo, RegraRepasse, Repasse, TabelaValor,
)
from apps.financeiro.servico import ErroFinanceiro
from apps.pacientes.models import Pagador
from apps.prontuario.armazenamento import armazenamento
from conftest import SENHA, entrar_com_2fa

pytestmark = pytest.mark.django_db

CPF = "52998224725"
D = Decimal


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dra. Ana", com_2fa=True)
    c.assistente = criar_usuario("sec@a.com", c.consultorio, Perfil.ASSISTENTE, nome="Sec")
    c.admin = criar_usuario("adm@a.com", c.consultorio, Perfil.ADMIN, nome="Admin")
    c.prof = c.dra.profissional
    c.paciente = criar_paciente(c.consultorio, "Maria da Silva", cpf=CPF, email="maria@x.com")
    c.req = lambda u=None: fazer_request(u or c.assistente, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)

    def valor(v, convenio=None, tipo="consulta"):
        return TabelaValor.objects.create(consultorio=c.consultorio, profissional=c.prof, convenio=convenio, tipo=tipo, valor=D(v))

    def regra(aplicacao="", percentual=None, fixo=None):
        return RegraRepasse.objects.create(consultorio=c.consultorio, profissional=c.prof, aplicacao=aplicacao, percentual=percentual, valor_fixo=fixo)

    def realizada(paciente=None, dias_atras=1, horas=0):
        inicio = timezone.now() - timedelta(days=dias_atras, hours=horas)
        (consulta,) = agenda.agendar(c.req(), profissional=c.prof, inicio=inicio, duracao_minutos=50, tipo="presencial", paciente=paciente or c.paciente)
        consulta = Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=consulta.pk)
        agenda.marcar_realizada(c.req(), consulta)
        return Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=consulta.pk)

    c.valor, c.regra, c.realizada = valor, regra, realizada
    return c


def login(client, usuario, c):
    if usuario.segundo_fator_ativo:
        entrar_com_2fa(client, usuario)
    else:
        client.post(reverse("contas:entrar"), {"email": usuario.email, "senha": SENHA})
    definir_contexto(consultorio_id=c.consultorio.pk)


# --------------------------------------------------------------------------- valores e lancamento


def test_consulta_realizada_gera_cobranca_automatica_com_o_valor_da_tabela(cena):
    cena.valor("200.00")
    consulta = cena.realizada()
    lancamento = Lancamento.objects.get(consulta=consulta)
    assert lancamento.valor == D("200.00") and lancamento.tipo == "particular" and lancamento.status == "pendente"
    assert lancamento.vencimento == timezone.localtime(consulta.inicio).date()


def test_sem_valor_configurado_nao_cria_cobranca(cena):
    cena.realizada()
    assert not Lancamento.objects.exists()


def test_primeira_consulta_usa_o_valor_proprio_e_o_retorno_o_valor_normal(cena):
    cena.valor("200.00")
    cena.valor("300.00", tipo="primeira_consulta")
    primeira = cena.realizada(dias_atras=3)
    retorno = cena.realizada(dias_atras=1)
    assert Lancamento.objects.get(consulta=primeira).valor == D("300.00")
    assert Lancamento.objects.get(consulta=retorno).valor == D("200.00")


def test_gerar_lancamento_e_idempotente(cena):
    cena.valor("200.00")
    consulta = cena.realizada()
    assert servico.gerar_lancamento(cena.req(), consulta).pk == Lancamento.objects.get().pk
    assert Lancamento.objects.count() == 1


def test_paciente_de_convenio_usa_a_tabela_da_operadora(cena, criar_paciente):
    unimed = Convenio.objects.create(consultorio=cena.consultorio, operadora="Unimed")
    cena.valor("200.00")
    cena.valor("120.00", convenio=unimed)
    conveniado = criar_paciente(cena.consultorio, "Carlos", convenio="unimed", carteirinha="123")
    consulta = cena.realizada(paciente=conveniado)
    lancamento = Lancamento.objects.get(consulta=consulta)
    assert lancamento.tipo == "convenio" and lancamento.valor == D("120.00") and lancamento.convenio == unimed
    atendimento = lancamento.atendimento_convenio
    assert atendimento.status == "realizado" and atendimento.carteirinha == "123"


def test_cancelamento_tardio_so_cobra_se_o_consultorio_optou(cena):
    cena.valor("200.00")
    (c1,) = agenda.agendar(cena.req(), profissional=cena.prof, inicio=timezone.now() + timedelta(hours=3), duracao_minutos=50, tipo="presencial", paciente=cena.paciente)
    agenda.cancelar(cena.req(), c1, "equipe", tardia=True)
    assert not Lancamento.objects.exists()

    cena.consultorio.cobra_falta_tardia = True
    cena.consultorio.save()
    (c2,) = agenda.agendar(cena.req(), profissional=cena.prof, inicio=timezone.now() + timedelta(hours=5), duracao_minutos=50, tipo="presencial", paciente=cena.paciente)
    c2 = Consulta.objects.select_related("consultorio", "paciente").get(pk=c2.pk)
    agenda.cancelar(cena.req(), c2, "equipe", tardia=True)
    lancamento = Lancamento.objects.get()
    assert lancamento.origem == "falta_tardia" and lancamento.valor == D("200.00")


# --------------------------------------------------------------------------- pagamento e repasse


def lancamento_pendente(cena, valor="100.10"):
    cena.valor(valor)
    consulta = cena.realizada()
    return Lancamento.objects.select_related("consultorio", "profissional__usuario").get(consulta=consulta)


def test_repasse_nasce_quando_o_dinheiro_entra(cena):
    cena.regra(percentual=D("30"))
    lancamento = lancamento_pendente(cena)
    assert not Repasse.objects.exists()  # cobrar nao gera repasse
    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    repasse = Repasse.objects.get()
    assert repasse.base == D("100.10") and repasse.valor == D("30.03") and repasse.status == "a_pagar"
    assert repasse.competencia == timezone.now().date().replace(day=1)


def test_regra_especifica_vence_a_padrao_e_primeira_consulta_vence_o_tipo(cena):
    cena.regra(percentual=D("50"))
    cena.regra("particular", percentual=D("40"))
    cena.regra("primeira_consulta", percentual=D("60"))
    cena.valor("100.00")
    primeira = cena.realizada(dias_atras=3)
    retorno = cena.realizada(dias_atras=1)
    for consulta in (primeira, retorno):
        servico.registrar_pagamento(cena.req(), Lancamento.objects.select_related("consultorio", "profissional").get(consulta=consulta), forma="pix")
    assert Repasse.objects.get(lancamento__consulta=primeira).valor == D("60.00")
    assert Repasse.objects.get(lancamento__consulta=retorno).valor == D("40.00")


def test_valor_fixo_nunca_passa_do_que_entrou(cena):
    cena.regra(fixo=D("150.00"))
    lancamento = lancamento_pendente(cena, "100.00")
    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    assert Repasse.objects.get().valor == D("100.00")


def test_sem_regra_nao_ha_repasse_e_aluguel_nunca_tem(cena):
    servico.registrar_pagamento(cena.req(), lancamento_pendente(cena), forma="pix")
    assert not Repasse.objects.exists()
    cena.regra(percentual=D("30"))
    aluguel = Lancamento.objects.create(
        consultorio=cena.consultorio, profissional=cena.prof, tipo="aluguel", origem="recorrente", valor=D("500"), vencimento=date.today()
    )
    servico.registrar_pagamento(cena.req(), aluguel, forma="transferencia")
    assert not Repasse.objects.exists()


def test_desfazer_pagamento_remove_repasse_a_pagar_mas_nao_o_pago(cena):
    cena.regra(percentual=D("30"))
    lancamento = lancamento_pendente(cena)
    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    servico.desfazer_pagamento(cena.req(), lancamento)
    assert lancamento.status == "pendente" and not Repasse.objects.exists()

    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    servico.marcar_repasse_pago(cena.req(cena.admin), Repasse.objects.get())
    with pytest.raises(ErroFinanceiro, match="repasse deste lançamento já foi pago"):
        servico.desfazer_pagamento(cena.req(), lancamento)
    with pytest.raises(ErroFinanceiro, match="já está pago"):
        servico.marcar_repasse_pago(cena.req(cena.admin), Repasse.objects.get())


def test_so_pendente_pode_ser_pago_ou_cancelado(cena):
    lancamento = lancamento_pendente(cena)
    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    with pytest.raises(ErroFinanceiro):
        servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    with pytest.raises(ErroFinanceiro):
        servico.cancelar_lancamento(cena.req(), lancamento)


# --------------------------------------------------------------------------- convenio


@pytest.fixture
def conveniado(cena, criar_paciente):
    unimed = Convenio.objects.create(consultorio=cena.consultorio, operadora="Unimed")
    cena.valor("120.00", convenio=unimed)
    cena.regra(percentual=D("50"))
    paciente = criar_paciente(cena.consultorio, "Carlos", convenio="Unimed", carteirinha="999")
    consulta = cena.realizada(paciente=paciente)
    return AtendimentoConvenio.objects.select_related("lancamento__consultorio", "lancamento__profissional").get(lancamento__consulta=consulta)


def test_convenio_retorno_total(cena, conveniado):
    servico.faturar(cena.req(), [conveniado], "Lote 1")
    conveniado.refresh_from_db()
    assert conveniado.status == "faturado" and conveniado.demonstrativo == "Lote 1"
    servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("120.00"))
    conveniado.refresh_from_db()
    assert conveniado.status == "pago" and conveniado.valor_glosado == 0
    assert conveniado.lancamento.status == "pago"
    assert Repasse.objects.get().valor == D("60.00")


def test_convenio_glosa_parcial_reduz_o_repasse_e_exige_motivo(cena, conveniado):
    servico.faturar(cena.req(), [conveniado])
    with pytest.raises(ErroFinanceiro, match="motivo"):
        servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("100.00"))
    servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("100.00"), motivo="Código divergente")
    conveniado.refresh_from_db()
    assert conveniado.status == "pago" and conveniado.valor_glosado == D("20.00") and conveniado.motivo_glosa == "Código divergente"
    repasse = Repasse.objects.get()
    assert repasse.base == D("100.00") and repasse.valor == D("50.00")


def test_convenio_glosa_total_nao_gera_repasse(cena, conveniado):
    servico.faturar(cena.req(), [conveniado])
    servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("0"), motivo="Sem cobertura")
    conveniado.refresh_from_db()
    assert conveniado.status == "glosado" and conveniado.valor_glosado == D("120.00")
    assert conveniado.lancamento.status == "pendente" and not Repasse.objects.exists()


def test_convenio_validacoes(cena, conveniado):
    with pytest.raises(ErroFinanceiro, match="faturados"):
        servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("120"))  # ainda nao faturado
    servico.faturar(cena.req(), [conveniado])
    with pytest.raises(ErroFinanceiro, match="entre zero"):
        servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("130"))
    with pytest.raises(ErroFinanceiro, match="Só atendimentos realizados"):
        servico.faturar(cena.req(), [AtendimentoConvenio.objects.get(pk=conveniado.pk)])
    with pytest.raises(ErroFinanceiro, match="retorno da operadora"):
        servico.registrar_pagamento(cena.req(), conveniado.lancamento, forma="pix")
    with pytest.raises(ErroFinanceiro, match="Convênio não gera recibo"):
        servico.emitir_recibo(cena.req(), conveniado.lancamento)


def test_nao_altera_retorno_depois_do_repasse_pago(cena, conveniado):
    servico.faturar(cena.req(), [conveniado])
    servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("120.00"))
    servico.marcar_repasse_pago(cena.req(cena.admin), Repasse.objects.get())
    conveniado.refresh_from_db()
    with pytest.raises(ErroFinanceiro):
        servico.registrar_retorno(cena.req(), conveniado, valor_recebido=D("60.00"), motivo="x")  # status ja e 'pago'


# --------------------------------------------------------------------------- recibo


def pago(cena, valor="200.00"):
    lancamento = lancamento_pendente(cena, valor)
    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    return Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=lancamento.pk)


def test_recibo_so_depois_de_pago_e_com_numeracao_sequencial(cena):
    lancamento = lancamento_pendente(cena)
    with pytest.raises(ErroFinanceiro, match="depois do pagamento"):
        servico.emitir_recibo(cena.req(), lancamento)
    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    primeiro = servico.emitir_recibo(cena.req(), Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=lancamento.pk))
    assert primeiro.numero == 1
    assert servico.emitir_recibo(cena.req(), primeiro.lancamento).pk == primeiro.pk  # idempotente

    segundo_lancamento = Lancamento.objects.create(
        consultorio=cena.consultorio, paciente=cena.paciente, profissional=cena.prof, tipo="particular", origem="manual", valor=D("50"), vencimento=date.today(),
    )
    servico.registrar_pagamento(cena.req(), segundo_lancamento, forma="dinheiro")
    assert servico.emitir_recibo(cena.req(), Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=segundo_lancamento.pk)).numero == 2


def test_numeracao_do_recibo_e_independente_por_consultorio(cena, criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    servico.emitir_recibo(cena.req(), pago(cena))
    outro = criar_consultorio("Outra Clínica")
    doutor = criar_usuario("dr@b.com", outro, Perfil.PROFISSIONAL, com_2fa=True)
    paciente = criar_paciente(outro, "Paciente B", cpf="11144477735")
    with contexto(consultorio_id=outro.pk):
        lancamento = Lancamento.objects.create(consultorio=outro, paciente=paciente, profissional=doutor.profissional, tipo="particular", origem="manual", valor=D("80"), vencimento=date.today())
        request = fazer_request(doutor, outro)
        servico.registrar_pagamento(request, lancamento, forma="pix")
        lancamento = Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=lancamento.pk)
        assert servico.emitir_recibo(request, lancamento).numero == 1


def test_recibo_sai_no_nome_do_pagador_e_o_pdf_fica_cifrado(cena, settings):
    Pagador.objects.create(consultorio=cena.consultorio, paciente=cena.paciente, nome="José Pagador", cpf="11144477735")
    lancamento = pago(cena)
    recibo = servico.emitir_recibo(cena.req(), lancamento)
    assert recibo.nome_pagador == "José Pagador" and recibo.cpf_pagador == "11144477735"
    bruto = armazenamento().ler(recibo.caminho)
    assert not bruto.startswith(b"%PDF")
    assert cripto.decifrar_bytes(bruto).startswith(b"%PDF")
    assert servico.baixar_recibo(cena.req(), recibo).startswith(b"%PDF")


def test_recibo_exige_cpf(cena, criar_paciente):
    sem_cpf = criar_paciente(cena.consultorio, "Sem CPF")
    cena.valor("100")
    consulta = cena.realizada(paciente=sem_cpf)
    lancamento = Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(consulta=consulta)
    servico.registrar_pagamento(cena.req(), lancamento, forma="pix")
    with pytest.raises(ErroFinanceiro, match="CPF"):
        servico.emitir_recibo(cena.req(), lancamento)


def test_nao_desfaz_pagamento_com_recibo_emitido(cena):
    lancamento = pago(cena)
    servico.emitir_recibo(cena.req(), lancamento)
    with pytest.raises(ErroFinanceiro, match="recibo emitido"):
        servico.desfazer_pagamento(cena.req(), lancamento)


def test_registro_da_nota_externa(cena):
    recibo = servico.emitir_recibo(cena.req(), pago(cena))
    servico.registrar_nota_externa(cena.req(), recibo, " 12345 ")
    recibo.refresh_from_db()
    assert recibo.numero_nota_externa == "12345"


# --------------------------------------------------------------------------- aluguel de sala


def test_aluguel_e_gerado_uma_vez_por_mes(cena):
    CobrancaRecorrente.objects.create(consultorio=cena.consultorio, profissional=cena.prof, valor=D("800"), dia_vencimento=5)
    primeiro = servico.gerar_cobrancas_do_mes(cena.req(), cena.consultorio, date(2026, 3, 20))
    assert len(primeiro) == 1 and primeiro[0].vencimento == date(2026, 3, 5) and primeiro[0].tipo == "aluguel"
    assert servico.gerar_cobrancas_do_mes(cena.req(), cena.consultorio, date(2026, 3, 28)) == []
    assert len(servico.gerar_cobrancas_do_mes(cena.req(), cena.consultorio, date(2026, 4, 1))) == 1
    assert Lancamento.objects.filter(tipo="aluguel").count() == 2


def test_comando_de_cobrancas_recorrentes(cena):
    CobrancaRecorrente.objects.create(consultorio=cena.consultorio, profissional=cena.prof, valor=D("800"), dia_vencimento=5)
    call_command("gerar_cobrancas_recorrentes")
    call_command("gerar_cobrancas_recorrentes")
    assert Lancamento.objects.filter(tipo="aluguel").count() == 1


# --------------------------------------------------------------------------- telas e permissoes


def test_profissional_ve_so_os_proprios_lancamentos_e_nao_opera(client, cena, criar_usuario):
    outra = criar_usuario("outra@a.com", cena.consultorio, Perfil.PROFISSIONAL, nome="Dra. Outra", com_2fa=True)
    meu = lancamento_pendente(cena)
    alheio = Lancamento.objects.create(consultorio=cena.consultorio, paciente=cena.paciente, profissional=outra.profissional, tipo="particular", origem="manual", valor=D("77"), vencimento=date.today())
    login(client, cena.dra, cena)
    assert client.get(reverse("financeiro:lancamento", args=[meu.pk])).status_code == 200
    assert client.get(reverse("financeiro:lancamento", args=[alheio.pk])).status_code == 404
    assert client.post(reverse("financeiro:lancamento", args=[meu.pk]), {"acao": "pagar", "forma_pagamento": "pix"}).status_code == 404
    assert client.get(reverse("financeiro:convenio")).status_code == 403
    assert client.get(reverse("financeiro:config_lista", args=["regras-repasse"])).status_code == 403


def test_assistente_registra_pagamento_pela_tela_e_emite_recibo(client, cena):
    lancamento = lancamento_pendente(cena)
    login(client, cena.assistente, cena)
    url = reverse("financeiro:lancamento", args=[lancamento.pk])
    assert client.post(url, {"acao": "pagar", "forma_pagamento": "pix"}).status_code == 302
    assert client.post(url, {"acao": "recibo"}).status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    recibo = Recibo.objects.get()
    baixado = client.get(reverse("financeiro:recibo_baixar", args=[recibo.pk]))
    assert baixado.content.startswith(b"%PDF") and "recibo-00001.pdf" in baixado["Content-Disposition"]


def test_lancamento_de_outro_consultorio_nao_aparece(client, cena, criar_consultorio, criar_usuario, criar_paciente):
    outro = criar_consultorio("Outra Clínica")
    doutor = criar_usuario("dr@b.com", outro, Perfil.PROFISSIONAL, com_2fa=True)
    paciente = criar_paciente(outro, "Paciente B")
    with contexto(consultorio_id=outro.pk):
        alheio = Lancamento.objects.create(consultorio=outro, paciente=paciente, profissional=doutor.profissional, tipo="particular", origem="manual", valor=D("10"), vencimento=date.today())
    login(client, cena.assistente, cena)
    assert client.get(reverse("financeiro:lancamento", args=[alheio.pk])).status_code == 404
    assert b"Paciente B" not in client.get(reverse("financeiro:lancamentos")).content


def test_admin_configura_regra_e_assistente_nao(client, cena):
    dados = {"profissional": str(cena.prof.pk), "aplicacao": "", "percentual": "40", "valor_fixo": ""}
    login(client, cena.assistente, cena)
    assert client.post(reverse("financeiro:config_novo", args=["regras-repasse"]), dados).status_code == 403
    client.logout()
    login(client, cena.admin, cena)
    assert client.post(reverse("financeiro:config_novo", args=["regras-repasse"]), dados).status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert RegraRepasse.objects.get().percentual == D("40.00")
    ambos = client.post(reverse("financeiro:config_novo", args=["regras-repasse"]), {**dados, "aplicacao": "convenio", "valor_fixo": "10"})
    assert ambos.status_code == 200 and "apenas um dos dois".encode() in ambos.content
    repetida = client.post(reverse("financeiro:config_novo", args=["regras-repasse"]), dados)
    assert repetida.status_code == 200 and b"existe" in repetida.content


def test_so_o_admin_marca_repasse_como_pago(client, cena):
    cena.regra(percentual=D("30"))
    servico.registrar_pagamento(cena.req(), lancamento_pendente(cena), forma="pix")
    repasse = Repasse.objects.get()
    login(client, cena.assistente, cena)
    assert client.post(reverse("financeiro:repasse_pagar", args=[repasse.pk])).status_code == 403
    client.logout()
    login(client, cena.admin, cena)
    assert client.post(reverse("financeiro:repasse_pagar", args=[repasse.pk])).status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Repasse.objects.get().status == "pago"


def test_profissional_ve_o_proprio_repasse_sem_botao_de_pagar(client, cena):
    cena.regra(percentual=D("30"))
    servico.registrar_pagamento(cena.req(), lancamento_pendente(cena), forma="pix")
    login(client, cena.dra, cena)
    pagina = client.get(reverse("financeiro:repasses"))
    assert pagina.status_code == 200 and b"30,03" in pagina.content and b"Marcar como pago" not in pagina.content


def test_fila_de_convenio_pelas_telas(client, cena, conveniado):
    login(client, cena.assistente, cena)
    assert b"Carlos" in client.get(reverse("financeiro:convenio")).content
    client.post(reverse("financeiro:convenio_faturar"), {"atendimento": [str(conveniado.pk)], "demonstrativo": "L7"})
    client.post(reverse("financeiro:convenio_retorno", args=[conveniado.pk]), {"valor_recebido": "100", "motivo": "Glosa de teste"})
    definir_contexto(consultorio_id=cena.consultorio.pk)
    conveniado.refresh_from_db()
    assert conveniado.status == "pago" and conveniado.valor_glosado == D("20.00")


def test_dinheiro_e_arredondado_a_centavo():
    assert servico.arredondar("10.005") == D("10.01") and servico.arredondar(D("33.3333")) == D("33.33")
