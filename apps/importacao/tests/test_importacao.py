import io
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook

from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil
from apps.core.tenancy import definir_contexto
from apps.importacao import servico
from apps.importacao.models import LoteImportacao
from apps.pacientes.models import Paciente
from conftest import SENHA, entrar_com_2fa

pytestmark = pytest.mark.django_db

CPF_A, CPF_B, CPF_C = "52998224725", "11144477735", "39053344705"
CABECALHO = "nome;cpf;nascimento;email;telefone;convenio;carteirinha\n"


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.assistente = criar_usuario("sec@a.com", c.consultorio, Perfil.ASSISTENTE, nome="Sec")
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dra. Ana", com_2fa=True)
    c.req = lambda u=None: fazer_request(u or c.assistente, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)
    return c


def csv(*linhas, cabecalho=CABECALHO, nome="pacientes.csv"):
    return SimpleUploadedFile(nome, (cabecalho + "\n".join(linhas)).encode("utf-8"))


def lote(cena, arquivo):
    return servico.criar_lote(cena.req(), arquivo)


def por_linha(l):
    return {x["linha"]: x for x in l.linhas}


def login(client, usuario, cena):
    if usuario.segundo_fator_ativo:
        entrar_com_2fa(client, usuario)
    else:
        client.post(reverse("contas:entrar"), {"email": usuario.email, "senha": SENHA})
    definir_contexto(consultorio_id=cena.consultorio.pk)


# --------------------------------------------------------------------------- leitura


def test_csv_com_ponto_e_virgula_valida_e_normaliza(cena):
    l = lote(cena, csv(f"Maria da Silva;529.982.247-25;15/03/1985;MARIA@Exemplo.com;(11) 98888-7777;Unimed;0012345"))
    linha = por_linha(l)[2]
    assert linha["status"] == "ok" and linha["mensagens"] == []
    assert linha["dados"] == {"nome": "Maria da Silva", "cpf": CPF_A, "nascimento": "1985-03-15", "email": "maria@exemplo.com", "telefone": "11988887777", "convenio": "Unimed", "carteirinha": "0012345"}
    assert (l.total, l.validas, l.duplicadas, l.com_erro) == (1, 1, 0, 0)


def test_csv_com_virgula_cabecalho_com_acento_e_latin1(cena):
    texto = "Nome Completo,Data de Nascimento,Celular,Convênio\nJoão da Conceição,02/11/1990,11977776666,Bradesco\n"
    arquivo = SimpleUploadedFile("p.csv", texto.encode("latin-1"))
    linha = por_linha(lote(cena, arquivo))[2]
    assert linha["status"] == "ok" and linha["dados"]["nome"] == "João da Conceição" and linha["dados"]["convenio"] == "Bradesco"


def test_xlsx_com_datas_e_cpf_numerico_sem_zero_a_esquerda(cena):
    wb = Workbook()
    ws = wb.active
    ws.append(["Nome", "CPF", "Nascimento", "Telefone"])
    ws.append(["Ana Souza", int(CPF_C), date(1992, 5, 20), 11988887777])
    ws.append(["Bia Lima", "01234567891", "20/05/1992", "(21) 97777-6666"])  # CPF invalido de proposito
    buffer = io.BytesIO()
    wb.save(buffer)
    l = lote(cena, SimpleUploadedFile("pacientes.xlsx", buffer.getvalue()))
    linhas = por_linha(l)
    assert linhas[2]["status"] == "ok" and linhas[2]["dados"]["cpf"] == CPF_C and linhas[2]["dados"]["nascimento"] == "1992-05-20"
    assert linhas[2]["dados"]["telefone"] == "11988887777"
    assert linhas[3]["status"] == "erro" and "CPF inválido." in linhas[3]["mensagens"]


def test_linhas_em_branco_sao_ignoradas(cena):
    l = lote(cena, csv("Maria;;;;;;", ";;;;;;", "", "João;;;;;;"))
    assert l.total == 2


# --------------------------------------------------------------------------- validacao


def test_erros_de_validacao_sao_apontados_por_linha(cena):
    l = lote(cena, csv(
        ";529.982.247-25;;;;;",                       # 2: sem nome
        "A;12345678900;;;;;",                         # 3: CPF invalido
        "B;;31/02/1990;;;;",                          # 4: data impossivel
        "C;;01/01/2999;;;;",                          # 5: nascimento no futuro
        "D;;;nao-e-email;;;",                         # 6: e-mail invalido
        "E;;;;123;;",                                 # 7: telefone curto
        "F;;;;;;",                                    # 8: ok (so nome)
    ))
    linhas = por_linha(l)
    assert "Nome vazio." in linhas[2]["mensagens"]
    assert "CPF inválido." in linhas[3]["mensagens"]
    assert any("Data de nascimento inválida" in m for m in linhas[4]["mensagens"])
    assert "Data de nascimento no futuro." in linhas[5]["mensagens"]
    assert "E-mail inválido." in linhas[6]["mensagens"]
    assert any("Telefone inválido" in m for m in linhas[7]["mensagens"])
    assert [linhas[n]["status"] for n in range(2, 9)] == ["erro"] * 6 + ["ok"]
    assert (l.validas, l.com_erro) == (1, 6)


def test_duplicados_do_cadastro_e_da_propria_planilha(cena, criar_paciente):
    criar_paciente(cena.consultorio, "Maria Antiga", cpf=CPF_A)
    l = lote(cena, csv(f"Maria Nova;{CPF_A};;;;;", f"Joana;{CPF_B};;;;;", f"Joana Repetida;{CPF_B};;;;;"))
    linhas = por_linha(l)
    assert linhas[2]["status"] == "duplicado" and "Maria Antiga" in linhas[2]["mensagens"][0]
    assert linhas[3]["status"] == "ok"
    assert linhas[4]["status"] == "duplicado" and "repetido" in linhas[4]["mensagens"][0]
    assert l.duplicadas == 2


def test_cpf_de_outro_consultorio_nao_e_duplicado(cena, criar_consultorio, criar_paciente):
    criar_paciente(criar_consultorio("Outra"), "Paciente de Outra", cpf=CPF_A)
    assert por_linha(lote(cena, csv(f"Maria;{CPF_A};;;;;")))[2]["status"] == "ok"


def test_mesmo_nome_e_nascimento_gera_aviso_sem_bloquear(cena, criar_paciente):
    criar_paciente(cena.consultorio, "Maria da Silva", nascimento=date(1985, 3, 15))
    linha = por_linha(lote(cena, csv("maria da silva;;15/03/1985;;;;")))[2]
    assert linha["status"] == "ok" and linha["avisos"] and "Confira" in linha["avisos"][0]


def test_paciente_mesclado_nao_conta_como_duplicado(cena, criar_paciente):
    from django.utils import timezone

    criar_paciente(cena.consultorio, "Arquivado", cpf=CPF_A, mesclado_em=timezone.now())
    assert por_linha(lote(cena, csv(f"Nova;{CPF_A};;;;;")))[2]["status"] == "ok"


# --------------------------------------------------------------------------- limites do arquivo


@pytest.mark.parametrize("arquivo,trecho", [
    (SimpleUploadedFile("p.pdf", b"%PDF"), "csv ou .xlsx"),
    (SimpleUploadedFile("vazia.csv", CABECALHO.encode()), "vazia"),
    (SimpleUploadedFile("sem_nome.csv", b"cpf;email\n52998224725;a@a.com\n"), "coluna"),
    (SimpleUploadedFile("quebrada.xlsx", b"isto nao e um xlsx"), "abrir a planilha"),
])
def test_arquivos_invalidos_sao_recusados_com_mensagem(cena, arquivo, trecho):
    with pytest.raises(servico.ErroImportacao, match=trecho):
        lote(cena, arquivo)
    assert not LoteImportacao.objects.exists()


def test_limites_de_tamanho_e_de_linhas(cena, settings):
    settings.PSIQ_IMPORTACAO_MAX_BYTES = 50
    with pytest.raises(servico.ErroImportacao, match="limite"):
        lote(cena, csv("A;;;;;;", "B;;;;;;", "C;;;;;;", "D;;;;;;"))
    settings.PSIQ_IMPORTACAO_MAX_BYTES = 5 * 1024 * 1024
    settings.PSIQ_IMPORTACAO_MAX_LINHAS = 2
    with pytest.raises(servico.ErroImportacao, match="mais de 2 linhas"):
        lote(cena, csv("A;;;;;;", "B;;;;;;", "C;;;;;;"))


# --------------------------------------------------------------------------- confirmacao


def test_confirmar_cria_so_as_linhas_prontas(cena, criar_paciente):
    criar_paciente(cena.consultorio, "Existente", cpf=CPF_A)
    l = lote(cena, csv(f"Ja Existe;{CPF_A};;;;;", f"Nova Pessoa;{CPF_B};10/10/1980;n@x.com;11999998888;Unimed;77", "Erro;12345678900;;;;;"))
    assert Paciente.objects.filter(consultorio=cena.consultorio).count() == 1  # a previa nao grava nada
    assert servico.confirmar(cena.req(), l) == 1
    nova = Paciente.objects.get(nome="Nova Pessoa")
    assert (nova.cpf, nova.nascimento, nova.email, nova.telefone, nova.convenio, nova.carteirinha) == (CPF_B, date(1980, 10, 10), "n@x.com", "11999998888", "Unimed", "77")
    l.refresh_from_db()
    assert l.status == "concluida" and l.importadas == 1
    with pytest.raises(servico.ErroImportacao, match="já foi concluída"):
        servico.confirmar(cena.req(), l)


def test_profissional_que_importa_fica_vinculado_aos_pacientes(cena):
    from apps.pacientes.servico import pacientes_visiveis

    l = lote(cena, csv("Paciente do Doutor;;;;;;"))
    request = cena.req(cena.dra)
    request.vinculo = type("V", (), {"perfil": Perfil.PROFISSIONAL})()
    servico.confirmar(request, l)
    assert pacientes_visiveis(request).filter(nome="Paciente do Doutor").exists()


def test_cpf_cadastrado_depois_da_previa_nao_duplica(cena, criar_paciente):
    l = lote(cena, csv(f"Maria;{CPF_A};;;;;"))
    criar_paciente(cena.consultorio, "Cadastrada no meio tempo", cpf=CPF_A)
    assert servico.confirmar(cena.req(), l) == 0
    assert Paciente.objects.filter(cpf=CPF_A).count() == 1
    l.refresh_from_db()
    assert l.duplicadas == 1


def test_cancelar_descarta_os_dados_da_planilha(cena):
    l = lote(cena, csv("Maria Sigilosa;;;;;;"))
    servico.cancelar(cena.req(), l)
    l.refresh_from_db()
    assert l.status == "cancelada" and l.linhas == [] and not Paciente.objects.filter(nome="Maria Sigilosa").exists()
    with pytest.raises(servico.ErroImportacao):
        servico.cancelar(cena.req(), l)


def test_auditoria_nao_guarda_dados_pessoais(cena):
    l = lote(cena, csv(f"Maria Pessoal;{CPF_A};;;;;"))
    servico.confirmar(cena.req(), l)
    for evento in Auditoria.objects.filter(acao__startswith="importacao"):
        assert "Maria" not in str(evento.detalhe) and CPF_A not in str(evento.detalhe)
    assert set(Auditoria.objects.values_list("acao", flat=True)) >= {"importacao_previa", "importacao_concluida"}


# --------------------------------------------------------------------------- telas


def test_fluxo_pelas_telas(client, cena):
    login(client, cena.assistente, cena)
    assert client.get(reverse("importacao:modelo")).content.decode("utf-8").startswith("﻿nome;cpf")
    pagina = client.post(reverse("importacao:inicio"), {"arquivo": csv(f"Maria;{CPF_A};;;;;", "Erro;123;;;;;")})
    assert pagina.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    l = LoteImportacao.objects.get()
    previa = client.get(reverse("importacao:previa", args=[l.pk])).content.decode()
    assert "1</strong> prontas" in previa and "Maria" in previa and "CPF inválido" in previa
    assert client.post(reverse("importacao:confirmar", args=[l.pk])).status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert Paciente.objects.filter(nome="Maria").exists()


def test_erro_de_arquivo_volta_para_a_tela_com_mensagem(client, cena):
    login(client, cena.assistente, cena)
    resposta = client.post(reverse("importacao:inicio"), {"arquivo": SimpleUploadedFile("x.pdf", b"%PDF")})
    assert resposta.status_code == 200 and "csv ou .xlsx".encode() in resposta.content


def test_lote_de_outro_consultorio_nao_abre(client, cena, criar_consultorio, criar_usuario, fazer_request):
    outro = criar_consultorio("Outra")
    admin_outro = criar_usuario("adm@b.com", outro, Perfil.ADMIN)
    definir_contexto(consultorio_id=outro.pk)
    alheio = servico.criar_lote(fazer_request(admin_outro, outro), csv("Paciente Alheio;;;;;;"))
    login(client, cena.assistente, cena)
    assert client.get(reverse("importacao:previa", args=[alheio.pk])).status_code == 404
    assert client.post(reverse("importacao:confirmar", args=[alheio.pk])).status_code == 404
