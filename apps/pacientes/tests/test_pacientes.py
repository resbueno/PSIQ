import pytest
from django.urls import reverse

from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil
from apps.core.tenancy import contexto, definir_contexto
from apps.core.validadores import cpf_valido
from apps.pacientes import servico
from apps.pacientes.models import Paciente, ProfissionalPaciente, ResponsavelLegal
from conftest import SENHA, entrar_com_2fa

pytestmark = pytest.mark.django_db

CPF_A = "52998224725"  # CPFs validos de teste
CPF_B = "11144477735"


def test_validacao_de_cpf():
    assert cpf_valido(CPF_A) and cpf_valido("529.982.247-25")
    assert not cpf_valido("52998224726")
    assert not cpf_valido("11111111111")
    assert not cpf_valido("123")


@pytest.fixture
def clinica(criar_consultorio, criar_usuario):
    consultorio = criar_consultorio("Clínica A")
    assistente = criar_usuario("sec@a.com", consultorio, Perfil.ASSISTENTE)
    return consultorio, assistente


def login(client, email):
    client.post(reverse("contas:entrar"), {"email": email, "senha": SENHA})


def test_assistente_cadastra_paciente_e_cpf_duplicado_e_recusado(client, clinica):
    consultorio, assistente = clinica
    login(client, assistente.email)
    dados = {"nome": "Ana Souza", "cpf": "529.982.247-25", "telefone": "(11) 98888-7777", "ativo": "on"}
    resposta = client.post(reverse("pacientes:novo"), dados)
    assert resposta.status_code == 302
    definir_contexto(consultorio_id=consultorio.pk)
    paciente = Paciente.objects.get(nome="Ana Souza")
    assert paciente.cpf == CPF_A and paciente.telefone == "11988887777"
    assert Auditoria.objects.filter(acao="paciente_criado").exists()

    repetido = client.post(reverse("pacientes:novo"), {**dados, "nome": "Outra Pessoa"})
    assert repetido.status_code == 200 and b"J\xc3\xa1 existe um paciente com este CPF" in repetido.content


def test_cpf_pode_repetir_em_outro_consultorio(client, clinica, criar_consultorio, criar_paciente):
    consultorio, assistente = clinica
    outro = criar_consultorio("Clínica B")
    criar_paciente(outro, "Ana em B", cpf=CPF_A)
    login(client, assistente.email)
    resposta = client.post(reverse("pacientes:novo"), {"nome": "Ana em A", "cpf": CPF_A, "ativo": "on"})
    assert resposta.status_code == 302


def test_cpf_invalido_e_recusado(client, clinica):
    _, assistente = clinica
    login(client, assistente.email)
    resposta = client.post(reverse("pacientes:novo"), {"nome": "X", "cpf": "12345678900", "ativo": "on"})
    assert resposta.status_code == 200 and b"CPF inv" in resposta.content


def test_paciente_de_outro_consultorio_nao_aparece(client, clinica, criar_consultorio, criar_paciente):
    consultorio, assistente = clinica
    outro = criar_consultorio("Clínica B")
    alheio = criar_paciente(outro, "Paciente Alheio")
    login(client, assistente.email)
    assert b"Paciente Alheio" not in client.get(reverse("pacientes:lista")).content
    assert client.get(reverse("pacientes:detalhe", args=[alheio.pk])).status_code == 404
    assert client.get(reverse("pacientes:editar", args=[alheio.pk])).status_code == 404


def test_profissional_ve_so_os_seus_pacientes(client, clinica, criar_usuario, criar_paciente):
    consultorio, _ = clinica
    doutora = criar_usuario("dra@a.com", consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    meu = criar_paciente(consultorio, "Paciente Meu")
    alheio = criar_paciente(consultorio, "Paciente de Outro")
    with contexto(consultorio_id=consultorio.pk):
        ProfissionalPaciente.objects.create(consultorio=consultorio, paciente=meu, profissional=doutora.profissional)
    entrar_com_2fa(client, doutora)
    pagina = client.get(reverse("pacientes:lista")).content
    assert b"Paciente Meu" in pagina and b"Paciente de Outro" not in pagina
    assert client.get(reverse("pacientes:detalhe", args=[alheio.pk])).status_code == 404


def test_profissional_que_cadastra_fica_vinculado(client, clinica, criar_usuario):
    consultorio, _ = clinica
    doutora = criar_usuario("dra@a.com", consultorio, Perfil.PROFISSIONAL, com_2fa=True)
    entrar_com_2fa(client, doutora)
    client.post(reverse("pacientes:novo"), {"nome": "Novo do Doutor", "ativo": "on"})
    assert b"Novo do Doutor" in client.get(reverse("pacientes:lista")).content


def test_responsavel_legal_e_pagador(client, clinica, criar_paciente):
    consultorio, assistente = clinica
    crianca = criar_paciente(consultorio, "Criança")
    login(client, assistente.email)
    client.post(reverse("pacientes:responsavel_novo", args=[crianca.pk]), {"nome": "Mãe", "email": "mae@x.com", "recebe_avisos": "on"})
    client.post(reverse("pacientes:pagador", args=[crianca.pk]), {"nome": "Avó", "cpf": CPF_B})
    definir_contexto(consultorio_id=consultorio.pk)
    assert ResponsavelLegal.objects.filter(paciente=crianca, nome="Mãe").exists()
    assert crianca.pagador.cpf == CPF_B


def test_mesclagem_preserva_historico(clinica, criar_paciente, fazer_request):
    consultorio, assistente = clinica
    destino = criar_paciente(consultorio, "Ana Souza")
    origem = criar_paciente(consultorio, "Ana S.", cpf=CPF_A, email="ana@x.com")
    with contexto(consultorio_id=consultorio.pk):
        ResponsavelLegal.objects.create(consultorio=consultorio, paciente=origem, nome="Mãe")
        servico.mesclar(fazer_request(assistente, consultorio), destino, origem)
        origem.refresh_from_db()
        destino.refresh_from_db()
        assert origem.mesclado_em and origem.mesclado_com_id == destino.pk and not origem.ativo
        assert ResponsavelLegal.objects.get(nome="Mãe").paciente_id == destino.pk
        assert destino.cpf == CPF_A and destino.email == "ana@x.com"  # preenche o que faltava
        assert Auditoria.objects.filter(acao="paciente_mesclado").exists()
        # o CPF do cadastro mesclado fica livre: nao ha conflito de unicidade
        assert Paciente.objects.filter(cpf=CPF_A, mesclado_em__isnull=True).count() == 1


def test_mesclagem_entre_consultorios_e_recusada(clinica, criar_consultorio, criar_paciente, fazer_request):
    consultorio, assistente = clinica
    outro = criar_consultorio("Clínica B")
    a = criar_paciente(consultorio, "A")
    b = criar_paciente(outro, "B")
    with pytest.raises(ValueError):
        servico.mesclar(fazer_request(assistente, consultorio), a, b)


def test_menor_de_idade():
    from datetime import date

    crianca = Paciente(nome="C", nascimento=date.today().replace(year=date.today().year - 10))
    adulto = Paciente(nome="A", nascimento=date.today().replace(year=date.today().year - 30))
    assert crianca.menor_de_idade and not adulto.menor_de_idade
    assert not Paciente(nome="Sem data").menor_de_idade


def test_edita_grupo_troca_participantes_e_exige_dois(client, clinica, criar_paciente):
    from apps.pacientes.models import GrupoAtendimento, ParticipanteGrupo

    consultorio, assistente = clinica
    a, b, c = (criar_paciente(consultorio, n) for n in ("Ana", "Beto", "Caio"))
    with contexto(consultorio_id=consultorio.pk):
        grupo = GrupoAtendimento.objects.create(consultorio=consultorio, tipo="casal", nome="Casal Ana e Beto")
        for p in (a, b):
            ParticipanteGrupo.objects.create(consultorio=consultorio, grupo=grupo, paciente=p)
    login(client, assistente.email)
    url = reverse("pacientes:grupo_editar", args=[grupo.pk])
    assert client.get(url).status_code == 200
    invalido = client.post(url, {"tipo": "casal", "nome": "Casal", "ativo": "on", "participantes": [str(a.pk)]})
    assert invalido.status_code == 200
    ok = client.post(url, {"tipo": "familia", "nome": "Família Ana", "ativo": "on", "participantes": [str(a.pk), str(c.pk)]})
    assert ok.status_code == 302
    definir_contexto(consultorio_id=consultorio.pk)
    grupo.refresh_from_db()
    assert grupo.tipo == "familia" and grupo.nome == "Família Ana"
    assert set(ParticipanteGrupo.objects.filter(grupo=grupo).values_list("paciente__nome", flat=True)) == {"Ana", "Caio"}
    assert Auditoria.objects.filter(acao="grupo_editado").exists()
