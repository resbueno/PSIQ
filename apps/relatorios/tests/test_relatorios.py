import io
import json
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.agenda import servico as agenda
from apps.agenda.models import AgendaRegra, Consulta
from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil
from apps.core.tenancy import contexto, definir_contexto
from apps.financeiro import servico as financeiro
from apps.financeiro.models import AtendimentoConvenio, Convenio, Lancamento, RegraRepasse, Repasse, TabelaValor
from apps.plataforma.models import Consultorio
from apps.prontuario import servico as prontuario
from apps.relatorios.models import Exportacao
from conftest import SENHA, entrar_com_2fa

pytestmark = pytest.mark.django_db

D = Decimal
SP = ZoneInfo("America/Sao_Paulo")
SEGREDO = "SENTINELA-CLINICA-ansiedade-F41.1"


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.admin = criar_usuario("adm@a.com", c.consultorio, Perfil.ADMIN, nome="Admin", com_2fa=True)
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dra. Ana", com_2fa=True)
    c.dr = criar_usuario("dr@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dr. Beto", com_2fa=True)
    c.assistente = criar_usuario("sec@a.com", c.consultorio, Perfil.ASSISTENTE, nome="Sec")
    c.maria = criar_paciente(c.consultorio, "Maria da Silva", cpf="52998224725", email="maria@x.com", telefone="11988887777")
    c.joao = criar_paciente(c.consultorio, "João Souza", cpf="11144477735", convenio="Unimed")
    c.req = lambda u=None: fazer_request(u or c.assistente, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)

    def consulta(prof, paciente, dias_atras, status="realizada", hora=10):
        base = (timezone.now().astimezone(SP) - timedelta(days=dias_atras)).replace(hour=hora, minute=0, second=0, microsecond=0)
        (x,) = agenda.agendar(c.req(), profissional=prof, inicio=base, duracao_minutos=60, tipo="presencial", paciente=paciente)
        x = Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=x.pk)
        if status == "realizada":
            agenda.marcar_realizada(c.req(), x)
        elif status == "faltou":
            agenda.marcar_falta(c.req(), x)
        elif status == "cancelada_paciente":
            Consulta.objects.filter(pk=x.pk).update(status="cancelada", cancelada_por="paciente")
        elif status == "cancelada_tardia":
            Consulta.objects.filter(pk=x.pk).update(status="cancelada", cancelada_por="equipe", falta_tardia=True)
        return Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=x.pk)

    c.consulta = consulta
    return c


def login(client, usuario, cena):
    if usuario.segundo_fator_ativo:
        entrar_com_2fa(client, usuario)
    else:
        client.post(reverse("contas:entrar"), {"email": usuario.email, "senha": SENHA})
    definir_contexto(consultorio_id=cena.consultorio.pk)


def periodo(inicio=40, fim=0):
    hoje = date.today()
    return {"de": (hoje - timedelta(days=inicio)).isoformat(), "ate": (hoje + timedelta(days=fim)).isoformat()}


def ver(client, tipo, **extra):
    return client.get(reverse("relatorios:ver", args=[tipo]), {**periodo(), **extra})


# --------------------------------------------------------------------------- agenda


def test_relatorio_de_agenda_conta_faltas_cancelamentos_e_ocupacao(client, cena):
    for dia in range(7):
        AgendaRegra.objects.create(consultorio=cena.consultorio, profissional=cena.dra.profissional, dia_semana=dia, hora_inicio=time(9), hora_fim=time(13), duracao_minutos=60)
    for i in (1, 2, 3):
        cena.consulta(cena.dra.profissional, cena.maria, dias_atras=i, hora=9 + i)
    cena.consulta(cena.dra.profissional, cena.maria, 4, status="faltou")
    cena.consulta(cena.dra.profissional, cena.maria, 5, status="cancelada_paciente")
    cena.consulta(cena.dra.profissional, cena.maria, 6, status="cancelada_tardia")
    login(client, cena.admin, cena)
    pagina = ver(client, "agenda").content.decode()
    assert "Dra. Ana" in pagina and "25,0%" in pagina  # 1 falta em 4 comparecimentos esperados
    assert "<td>6</td>" in pagina and "<td>2</td>" in pagina  # 6 consultas, 2 cancelamentos
    assert "Taxa de faltas" in pagina and "Ocupação" in pagina


def test_profissional_ve_so_os_proprios_numeros(client, cena):
    cena.consulta(cena.dra.profissional, cena.maria, 1)
    cena.consulta(cena.dr.profissional, cena.joao, 1)
    login(client, cena.dra, cena)
    pagina = ver(client, "agenda").content.decode()
    assert "Dra. Ana" in pagina and "Dr. Beto" not in pagina


# --------------------------------------------------------------------------- financeiro


def lancamento(cena, prof, paciente, valor, dias_vencimento=0, status="pendente", tipo="particular"):
    l = Lancamento.objects.create(
        consultorio=cena.consultorio, paciente=paciente, profissional=prof, tipo=tipo, origem="manual", valor=D(valor),
        vencimento=date.today() + timedelta(days=dias_vencimento),
    )
    if status == "pago":
        financeiro.registrar_pagamento(cena.req(), l, forma="pix")
    return Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=l.pk)


def test_faturamento_separa_recebido_e_a_receber(client, cena):
    lancamento(cena, cena.dra.profissional, cena.maria, "300.00", status="pago")
    lancamento(cena, cena.dra.profissional, cena.maria, "100.00", dias_vencimento=-3)
    lancamento(cena, cena.dr.profissional, cena.joao, "200.00", status="pago")
    login(client, cena.admin, cena)
    pagina = ver(client, "faturamento").content.decode()
    assert "R$ 500,00" in pagina and "R$ 100,00" in pagina and "Dr. Beto" in pagina
    client.logout()  # profissional enxerga so o proprio
    login(client, cena.dra, cena)
    so_dela = ver(client, "faturamento").content.decode()
    assert "Dra. Ana" in so_dela and "Dr. Beto" not in so_dela and "R$ 300,00" in so_dela


def test_inadimplencia_lista_nomes_e_audita(client, cena):
    lancamento(cena, cena.dra.profissional, cena.maria, "150.00", dias_vencimento=-10)
    lancamento(cena, cena.dra.profissional, cena.joao, "90.00", dias_vencimento=5)  # nao venceu
    login(client, cena.assistente, cena)
    pagina = ver(client, "inadimplencia").content.decode()
    assert "Maria da Silva" in pagina and "João Souza" not in pagina and "<td>10</td>" in pagina
    definir_contexto(consultorio_id=cena.consultorio.pk)
    evento = Auditoria.objects.get(acao="relatorio_com_nomes")
    assert evento.objeto_id == "inadimplencia" and evento.usuario_id == cena.assistente.pk


def test_relatorio_sem_nomes_nao_gera_auditoria_de_nomes(client, cena):
    cena.consulta(cena.dra.profissional, cena.maria, 1)
    login(client, cena.admin, cena)
    ver(client, "agenda")
    ver(client, "pacientes-resumo")
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert not Auditoria.objects.filter(acao="relatorio_com_nomes").exists()


def test_repasses_por_perfil(client, cena):
    RegraRepasse.objects.create(consultorio=cena.consultorio, profissional=cena.dra.profissional, percentual=D("40"))
    RegraRepasse.objects.create(consultorio=cena.consultorio, profissional=cena.dr.profissional, percentual=D("50"))
    lancamento(cena, cena.dra.profissional, cena.maria, "100.00", status="pago")
    lancamento(cena, cena.dr.profissional, cena.joao, "100.00", status="pago")
    login(client, cena.assistente, cena)
    assert ver(client, "repasses").status_code == 404  # assistente nao ve repasses
    client.logout()
    login(client, cena.admin, cena)
    todos = ver(client, "repasses").content.decode()
    assert "Dra. Ana" in todos and "Dr. Beto" in todos and "40,00" in todos and "50,00" in todos
    client.logout()
    login(client, cena.dra, cena)
    proprio = ver(client, "repasses").content.decode()
    assert "Dra. Ana" in proprio and "Dr. Beto" not in proprio


def test_glosas_por_operadora(client, cena):
    unimed = Convenio.objects.create(consultorio=cena.consultorio, operadora="Unimed")
    TabelaValor.objects.create(consultorio=cena.consultorio, profissional=cena.dra.profissional, convenio=unimed, valor=D("100"))
    consulta = cena.consulta(cena.dra.profissional, cena.joao, 2)
    atendimento = AtendimentoConvenio.objects.select_related("lancamento__consultorio", "lancamento__profissional").get(lancamento__consulta=consulta)
    financeiro.faturar(cena.req(), [atendimento])
    financeiro.registrar_retorno(cena.req(), atendimento, valor_recebido=D("80"), motivo="Glosa parcial")
    login(client, cena.admin, cena)
    pagina = ver(client, "glosas").content.decode()
    assert "Unimed" in pagina and "20,00" in pagina and "20,0%" in pagina


# --------------------------------------------------------------------------- pacientes


def test_pacientes_novos_e_sem_consulta(client, cena):
    cena.consulta(cena.dra.profissional, cena.maria, 5)  # Maria tem atendimento recente; Joao nunca teve
    login(client, cena.admin, cena)
    novos = ver(client, "pacientes-novos").content.decode()
    assert "Maria da Silva" in novos and "João Souza" in novos
    sem = ver(client, "pacientes-sem-consulta", dias=30).content.decode()
    assert "João Souza" in sem and "Nunca" in sem and "Maria da Silva" not in sem
    resumo = ver(client, "pacientes-resumo").content.decode()
    assert "Pacientes ativos" in resumo and "Ativos com convênio" in resumo and "Maria da Silva" not in resumo


# --------------------------------------------------------------------------- saidas e privacidade


def test_csv_tem_bom_ponto_e_virgula_e_protege_contra_formulas(client, cena, criar_paciente):
    criar_paciente(cena.consultorio, "=HYPERLINK(\"http://x\")", email="m@x.com")
    login(client, cena.admin, cena)
    resposta = ver(client, "pacientes-novos", formato="csv")
    assert resposta["Content-Type"].startswith("text/csv") and "attachment" in resposta["Content-Disposition"]
    texto = resposta.content.decode("utf-8")
    assert texto.startswith("﻿") and "Paciente;Cadastro;Convênio" in texto
    assert "'=HYPERLINK" in texto and ";=HYPERLINK" not in texto
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Auditoria.objects.filter(acao="relatorio_exportado", objeto_id="pacientes-novos").exists()


def test_pdf_dos_relatorios(client, cena):
    cena.consulta(cena.dra.profissional, cena.maria, 1)
    login(client, cena.admin, cena)
    for tipo in ("agenda", "faturamento", "pacientes-novos", "pacientes-resumo"):
        resposta = ver(client, tipo, formato="pdf")
        assert resposta.content.startswith(b"%PDF") and "application/pdf" in resposta["Content-Type"]


def test_nenhum_relatorio_traz_conteudo_clinico_ou_cid(client, cena):
    pront = prontuario.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=cena.maria)
    prontuario.criar_registro(cena.req(cena.dra), pront, tipo="evolucao", conteudo=SEGREDO, cid="F41.1")
    cena.consulta(cena.dra.profissional, cena.maria, 2)
    lancamento(cena, cena.dra.profissional, cena.maria, "100", dias_vencimento=-2)
    login(client, cena.admin, cena)
    from apps.relatorios.servico import TIPOS

    for tipo in TIPOS:
        for formato in ("html", "csv"):
            resposta = ver(client, tipo, formato=formato)
            assert resposta.status_code == 200, tipo
            assert SEGREDO.encode() not in resposta.content and b"F41" not in resposta.content and b"ansiedade" not in resposta.content


def test_periodo_invalido_usa_o_padrao_e_invertido_e_corrigido(client, cena):
    login(client, cena.admin, cena)
    assert client.get(reverse("relatorios:ver", args=["agenda"]), {"de": "lixo", "ate": "tambem"}).status_code == 200
    hoje = date.today()
    invertido = client.get(reverse("relatorios:ver", args=["agenda"]), {"de": hoje.isoformat(), "ate": (hoje - timedelta(days=10)).isoformat()})
    assert invertido.status_code == 200
    assert client.get(reverse("relatorios:ver", args=["inexistente"])).status_code == 404


def test_relatorios_isolados_por_consultorio(client, cena, criar_consultorio, criar_usuario, criar_paciente):
    outro = criar_consultorio("Outra Clínica")
    estranha = criar_paciente(outro, "Paciente da Outra Clínica")
    outra = criar_usuario("dra@b.com", outro, Perfil.PROFISSIONAL, com_2fa=True)
    with contexto(consultorio_id=outro.pk):
        Lancamento.objects.create(consultorio=outro, paciente=estranha, profissional=outra.profissional, tipo="particular", origem="manual", valor=D("999"), vencimento=date.today() - timedelta(days=5))
    login(client, cena.admin, cena)
    for tipo in ("inadimplencia", "pacientes-novos", "faturamento"):
        assert b"Outra" not in ver(client, tipo).content and b"999" not in ver(client, tipo).content


# --------------------------------------------------------------------------- exportacao


def abrir_zip(resposta):
    return zipfile.ZipFile(io.BytesIO(resposta.content))


def test_exportacao_do_consultorio_tem_tudo_menos_prontuarios(client, cena):
    pront = prontuario.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=cena.maria)
    prontuario.criar_registro(cena.req(cena.dra), pront, tipo="evolucao", conteudo=SEGREDO, cid="F41.1")
    prontuario.emitir_documento_clinico(cena.req(cena.dra), pront, titulo="Relatório sigiloso", texto=SEGREDO)
    TabelaValor.objects.create(consultorio=cena.consultorio, profissional=cena.dra.profissional, valor=D("200"))
    consulta = cena.consulta(cena.dra.profissional, cena.maria, 1)
    pago = Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(consulta=consulta)
    financeiro.registrar_pagamento(cena.req(), pago, forma="pix")
    financeiro.emitir_recibo(cena.req(), Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=pago.pk))
    prontuario.emitir_comparecimento(cena.req(), consulta)

    login(client, cena.admin, cena)
    resposta = client.post(reverse("relatorios:exportar_consultorio"))
    assert resposta["Content-Type"] == "application/zip" and "no-store" in resposta["Cache-Control"]
    z = abrir_zip(resposta)
    nomes = z.namelist()
    for esperado in ("pacientes.csv", "consultas.csv", "lancamentos.csv", "repasses.csv", "usuarios.csv", "auditoria.csv", "dados.json", "LEIA-ME.txt", "recibos/recibo-00001.pdf"):
        assert esperado in nomes, esperado
    assert any(n.startswith("declaracoes/") for n in nomes)
    assert z.read("recibos/recibo-00001.pdf").startswith(b"%PDF")
    pacientes = z.read("pacientes.csv").decode("utf-8")
    assert "Maria da Silva" in pacientes and "52998224725" in pacientes
    dados = json.loads(z.read("dados.json"))
    assert {p["nome"] for p in dados["tabelas"]["pacientes"]} == {"Maria da Silva", "João Souza"}
    for nome in nomes:  # nada de conteudo clinico em lugar nenhum
        assert SEGREDO.encode() not in z.read(nome), nome
    assert not any("sigiloso" in n for n in nomes)
    assert "usuarios.csv" in nomes and b"senha" not in z.read("usuarios.csv").lower()

    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Exportacao.objects.get().tipo == "consultorio"
    assert Auditoria.objects.filter(acao="exportacao_gerada").exists()


def test_so_o_admin_exporta_o_consultorio(client, cena):
    login(client, cena.assistente, cena)
    assert client.post(reverse("relatorios:exportar_consultorio")).status_code == 403
    assert client.get(reverse("relatorios:exportacao")).status_code == 200


def test_profissional_exporta_os_proprios_prontuarios_decifrados(client, cena):
    pront = prontuario.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=cena.maria)
    registro = prontuario.criar_registro(cena.req(cena.dra), pront, tipo="evolucao", conteudo=SEGREDO, cid="F41.1")
    prontuario.nova_versao(cena.req(cena.dra), registro, conteudo=SEGREDO + " (revisado)", cid="F41.1")
    prontuario.anexar(cena.req(cena.dra), pront, SimpleUploadedFile("exame.txt", b"conteudo do exame"))
    prontuario.emitir_documento_clinico(cena.req(cena.dra), pront, titulo="Atestado", texto="Atesto.")
    outro = prontuario.abrir_prontuario(cena.req(cena.dr), cena.dr.profissional, paciente=cena.joao)
    prontuario.criar_registro(cena.req(cena.dr), outro, tipo="evolucao", conteudo="PRONTUARIO-DO-OUTRO")

    login(client, cena.dra, cena)
    resposta = client.post(reverse("relatorios:exportar_prontuarios"))
    z = abrir_zip(resposta)
    nomes = z.namelist()
    registros = json.loads(z.read([n for n in nomes if n.endswith("registros.json")][0]))
    assert registros["paciente"] == "Maria da Silva"
    versoes = registros["registros"][0]["versoes"]
    assert [v["numero"] for v in versoes] == [1, 2] and versoes[0]["conteudo"] == SEGREDO and versoes[0]["cid"] == "F41.1"
    assert any(n.endswith(".txt") and z.read(n) == b"conteudo do exame" for n in nomes)
    assert any(n.endswith(".pdf") and z.read(n).startswith(b"%PDF") for n in nomes)
    assert all(b"PRONTUARIO-DO-OUTRO" not in z.read(n) for n in nomes)

    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Auditoria.objects.filter(acao="prontuarios_exportados").exists()
    assert Exportacao.objects.get(tipo="prontuarios").arquivos >= 3


def test_exportar_prontuarios_so_para_profissional(client, cena):
    login(client, cena.admin, cena)
    assert client.post(reverse("relatorios:exportar_prontuarios")).status_code == 403


# --------------------------------------------------------------------------- exportacao sempre liberada


def test_exportacao_funciona_em_carencia_e_somente_leitura(client, cena):
    login(client, cena.admin, cena)
    for situacao in ("carencia", "somente_leitura"):
        Consultorio.objects.filter(pk=cena.consultorio.pk).update(status=situacao)
        assert client.post(reverse("relatorios:exportar_consultorio")).status_code == 200
        definir_contexto(consultorio_id=cena.consultorio.pk)


def test_contrato_encerrado_da_90_dias_so_para_exportar(client, cena):
    Consultorio.objects.filter(pk=cena.consultorio.pk).update(status="encerrado", encerrado_em=timezone.now() - timedelta(days=10))
    login(client, cena.admin, cena)
    assert client.get(reverse("painel")).status_code == 302 and client.get(reverse("painel")).url == reverse("relatorios:exportacao")
    assert client.get(reverse("pacientes:lista")).status_code == 302
    assert client.get(reverse("relatorios:exportacao")).status_code == 200
    assert client.post(reverse("relatorios:exportar_consultorio")).status_code == 200


def test_depois_de_90_dias_o_acesso_fecha(client, cena):
    Consultorio.objects.filter(pk=cena.consultorio.pk).update(status="encerrado", encerrado_em=timezone.now() - timedelta(days=91))
    login(client, cena.admin, cena)
    assert client.get(reverse("relatorios:exportacao")).status_code == 302
    assert client.get(reverse("relatorios:exportacao")).url == reverse("contas:escolher_consultorio")
