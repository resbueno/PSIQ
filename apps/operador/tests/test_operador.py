from datetime import date, timedelta
from decimal import Decimal

import pyotp
import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil, Usuario
from apps.core.tenancy import contexto, definir_contexto
from apps.operador import servico
from apps.operador.models import AcessoSuporte
from apps.operador.servico import ErroOperador
from apps.plataforma.models import Consultorio, PagamentoPlataforma, Plano
from apps.prontuario import servico as prontuario
from conftest import SENHA, entrar_com_2fa

pytestmark = pytest.mark.django_db


def criar_operador(email="op@psiq.com", com_2fa=True):
    u = Usuario.objects.create_user(email, SENHA, nome="Atendente", is_staff=True)
    if com_2fa:
        segredo = pyotp.random_base32()
        u.definir_segredo_2fa(segredo)
        u.segundo_fator_ativo = True
        u.save()
        u.segredo_teste = segredo
    return u


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    class Cena:
        pass

    c = Cena()
    plano = Plano.objects.create(nome="Essencial", limite_pacientes_ativos=1, limite_profissionais=1, preco=Decimal("99"))
    c.consultorio = criar_consultorio("Clínica Aurora", plano=plano)
    c.admin = criar_usuario("adm@a.com", c.consultorio, Perfil.ADMIN, nome="Admin", com_2fa=True)
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dra. Ana", com_2fa=True)
    c.maria = criar_paciente(c.consultorio, "Maria Segredo da Silva", email="maria@x.com")
    c.op = criar_operador()
    c.req = lambda u: fazer_request(u, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)
    return c


def cobranca(cenario, dias_vencida, competencia=None, **extra):
    return PagamentoPlataforma.objects.create(
        consultorio=cenario.consultorio, competencia=competencia or date.today().replace(day=1) - timedelta(days=dias_vencida),
        valor=Decimal("99"), vencimento=date.today() - timedelta(days=dias_vencida), **extra,
    )


def status(cenario):
    return Consultorio.objects.get(pk=cenario.consultorio.pk).status


# --------------------------------------------------------------------------- inadimplencia


def test_sem_pendencia_continua_ativo(cena):
    assert servico.recalcular_situacao(cena.consultorio) is None and status(cena) == "ativo"


def test_vencimento_futuro_nao_conta(cena):
    cobranca(cena, dias_vencida=-5)
    assert servico.recalcular_situacao(cena.consultorio) is None and status(cena) == "ativo"


def test_pendencia_vencida_entra_em_carencia_e_avisa_o_admin(cena):
    mail.outbox.clear()
    cobranca(cena, dias_vencida=3)
    assert servico.recalcular_situacao(cena.consultorio) == "carencia"
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["adm@a.com"]
    corpo = mail.outbox[0].body
    assert "pendente" in corpo and "exportação nunca é bloqueada" in corpo
    assert servico.recalcular_situacao(cena.consultorio) is None and len(mail.outbox) == 1  # nao repete o aviso


def test_depois_de_15_dias_vira_somente_leitura(cena):
    mail.outbox.clear()
    cobranca(cena, dias_vencida=14)
    assert servico.recalcular_situacao(cena.consultorio) == "carencia"
    PagamentoPlataforma.objects.update(vencimento=date.today() - timedelta(days=15))
    assert servico.recalcular_situacao(cena.consultorio) == "somente_leitura"
    assert "somente leitura" in mail.outbox[-1].body


def test_pagar_restaura_o_acesso(cena):
    pagamento = cobranca(cena, dias_vencida=20)
    servico.recalcular_situacao(cena.consultorio)
    assert status(cena) == "somente_leitura"
    assert servico.marcar_pago(pagamento) == "ativo" and status(cena) == "ativo"
    assert PagamentoPlataforma.objects.get().pago_em is not None
    with pytest.raises(ErroOperador, match="já está marcado"):
        servico.marcar_pago(pagamento)


def test_a_mais_antiga_define_a_situacao_e_encerrado_nao_e_tocado(cena):
    cobranca(cena, dias_vencida=40)
    cobranca(cena, dias_vencida=2, competencia=date.today().replace(day=1))
    assert servico.recalcular_situacao(cena.consultorio) == "somente_leitura"
    Consultorio.objects.filter(pk=cena.consultorio.pk).update(status="encerrado")
    assert servico.recalcular_situacao(cena.consultorio) is None and status(cena) == "encerrado"


def test_comando_aplica_a_regra_em_todos_os_consultorios(cena, criar_consultorio):
    outro = criar_consultorio("Outra")
    cobranca(cena, dias_vencida=30)
    call_command("atualizar_inadimplencia")
    assert status(cena) == "somente_leitura" and Consultorio.objects.get(pk=outro.pk).status == "ativo"


def test_cobranca_duplicada_na_mesma_competencia_e_recusada(cena):
    servico.criar_pagamento(cena.consultorio, competencia=date(2026, 3, 15), valor=99, vencimento=date(2026, 3, 10))
    with pytest.raises(ErroOperador, match="Já existe"):
        servico.criar_pagamento(cena.consultorio, competencia=date(2026, 3, 1), valor=99, vencimento=date(2026, 3, 10))
    assert PagamentoPlataforma.objects.get().competencia == date(2026, 3, 1)


def test_somente_leitura_bloqueia_escrita_mas_nao_a_leitura(client, cena):
    Consultorio.objects.filter(pk=cena.consultorio.pk).update(status="somente_leitura")
    entrar_com_2fa(client, cena.admin)
    assert client.get(reverse("contas:usuarios")).status_code == 200
    assert client.post(reverse("contas:novo_usuario"), {"nome": "X", "email": "x@x.com", "perfil": "assistente"}).status_code == 403


def test_metadados_contam_sem_ler_conteudo_clinico(cena):
    meta = servico.metadados(cena.consultorio)
    assert meta["pacientes"] == 1 and meta["profissionais"] == 1 and meta["usuarios"] == 2 and not meta["acima_do_limite"]
    from apps.pacientes.models import Paciente

    with contexto(consultorio_id=cena.consultorio.pk):
        Paciente.objects.create(consultorio=cena.consultorio, nome="Segundo")
    assert servico.metadados(cena.consultorio)["acima_do_limite"]


# --------------------------------------------------------------------------- painel do operador


def login_operador(client, cena):
    entrar_com_2fa(client, cena.op)


def test_painel_so_para_equipe_com_2fa(client, cena):
    entrar_com_2fa(client, cena.admin)
    assert client.get(reverse("operador:painel")).status_code == 403  # admin de consultorio nao e equipe
    client.logout()
    sem_2fa = criar_operador("op2@psiq.com", com_2fa=False)
    client.post(reverse("contas:entrar"), {"email": sem_2fa.email, "senha": SENHA})
    resposta = client.get(reverse("operador:painel"))
    assert resposta.status_code == 302 and resposta.url == reverse("contas:configurar_2fa")


def test_equipe_sem_2fa_tambem_e_barrada_no_admin_do_django(client, db):
    sem_2fa = criar_operador("op2@psiq.com", com_2fa=False)
    client.post(reverse("contas:entrar"), {"email": sem_2fa.email, "senha": SENHA})
    resposta = client.get("/admin/")
    assert resposta.status_code == 302 and resposta.url == reverse("contas:configurar_2fa")


def test_login_da_equipe_sem_consultorio_cai_no_painel(client, cena):
    client.post(reverse("contas:entrar"), {"email": cena.op.email, "senha": SENHA})
    resposta = client.post(reverse("contas:verificar_2fa"), {"codigo": pyotp.TOTP(cena.op.segredo_teste).now()})
    assert resposta.status_code == 302 and resposta.url == reverse("operador:painel")


def test_painel_mostra_numeros_e_nunca_dados_de_pacientes(client, cena):
    prontuario.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=cena.maria)
    cobranca(cena, dias_vencida=2)
    login_operador(client, cena)
    pagina = client.get(reverse("operador:painel")).content.decode()
    assert "Clínica Aurora" in pagina and "1 vencida" in pagina and "Segredo" not in pagina
    detalhe = client.get(reverse("operador:consultorio", args=[cena.consultorio.pk])).content.decode()
    assert "1 pacientes ativos" in detalhe and "Segredo" not in detalhe and "maria@x.com" not in detalhe


def test_operador_altera_plano_status_e_cobranca(client, cena):
    login_operador(client, cena)
    url = reverse("operador:alterar", args=[cena.consultorio.pk])
    novo = Plano.objects.create(nome="Plus", limite_pacientes_ativos=50, limite_profissionais=5, preco=Decimal("199"))
    client.post(url, {"acao": "plano", "plano": str(novo.pk)})
    assert Consultorio.objects.get(pk=cena.consultorio.pk).plano_id == novo.pk
    client.post(url, {"acao": "pagamento", "competencia": (date.today() - timedelta(days=40)).replace(day=1).isoformat(), "valor": "99", "vencimento": (date.today() - timedelta(days=30)).isoformat()})
    assert status(cena) == "somente_leitura"  # cobranca vencida ha 30 dias aplica a regra na hora
    pagamento = PagamentoPlataforma.objects.get()
    client.post(reverse("operador:pagar", args=[cena.consultorio.pk, pagamento.pk]))
    assert status(cena) == "ativo"
    client.post(url, {"acao": "status", "status": "encerrado"})
    consultorio = Consultorio.objects.get(pk=cena.consultorio.pk)
    assert consultorio.status == "encerrado" and consultorio.encerrado_em is not None


def test_operador_cria_consultorio_e_o_admin_consegue_entrar(client, cena):
    login_operador(client, cena)
    resposta = client.post(reverse("operador:novo"), {
        "nome": "Clínica Nova", "documento": "", "plano": "", "admin_nome": "Nova Admin",
        "admin_email": "nova@clinica.com", "admin_senha": "senha-bem-longa-789",
    })
    assert resposta.status_code == 302
    client.logout()
    entrar = client.post(reverse("contas:entrar"), {"email": "nova@clinica.com", "senha": "senha-bem-longa-789"})
    assert entrar.status_code == 302 and entrar.url == reverse("painel")
    assert "Clínica Nova".encode() in client.get(reverse("painel")).content


def test_senha_fraca_ou_email_repetido_no_novo_consultorio(client, cena):
    login_operador(client, cena)
    base = {"nome": "X", "documento": "", "plano": "", "admin_nome": "A", "admin_email": "novo@x.com", "admin_senha": "curta"}
    assert client.post(reverse("operador:novo"), base).status_code == 200
    repetido = client.post(reverse("operador:novo"), {**base, "admin_email": "adm@a.com", "admin_senha": "senha-bem-longa-789"})
    assert repetido.status_code == 200 and "Já existe".encode() in repetido.content


def test_comando_criar_consultorio_continua_funcionando(db):
    call_command("criar_consultorio", nome="Via Comando", admin_email="cmd@x.com", admin_nome="Cmd", admin_senha="senha-bem-longa-789")
    assert Consultorio.objects.filter(nome="Via Comando").exists()


# --------------------------------------------------------------------------- suporte autorizado pelo cliente


def autorizar(client, cena, horas="1", email=None):
    entrar_com_2fa(client, cena.admin)
    resposta = client.post(reverse("operador:suporte_cliente"), {"operador_email": email or cena.op.email, "horas": horas, "motivo": "Dúvida sobre a agenda"})
    client.logout()
    definir_contexto(consultorio_id=cena.consultorio.pk)
    return resposta


def test_sem_autorizacao_o_operador_nao_entra(client, cena):
    login_operador(client, cena)
    resposta = client.post(reverse("operador:suporte_entrar", args=[cena.consultorio.pk]), follow=True)
    assert "ainda não autorizou".encode() in resposta.content
    assert client.get(reverse("contas:usuarios")).status_code in (302, 403)


def test_cliente_autoriza_e_o_acesso_e_somente_leitura_e_auditado(client, cena):
    assert autorizar(client, cena).status_code == 302
    acesso = AcessoSuporte.objects.get()
    assert acesso.vigente and acesso.operador_id == cena.op.pk and acesso.autorizado_por_id == cena.admin.pk

    login_operador(client, cena)
    assert client.post(reverse("operador:suporte_entrar", args=[cena.consultorio.pk])).status_code == 302
    assert "Modo suporte".encode() in client.get(reverse("painel")).content
    assert client.get(reverse("contas:usuarios")).status_code == 200
    escrita = client.post(reverse("contas:novo_usuario"), {"nome": "X", "email": "x@x.com", "perfil": "assistente", "senha": "senha-bem-longa-789"})
    assert escrita.status_code == 403
    assert not Usuario.objects.filter(email="x@x.com").exists()

    client.post(reverse("operador:suporte_sair"))
    definir_contexto(consultorio_id=cena.consultorio.pk)
    acoes = list(Auditoria.objects.values_list("acao", flat=True))
    assert "suporte_autorizado" in acoes and "suporte_entrou" in acoes and acoes.count("suporte_requisicao") >= 2
    assert client.get(reverse("contas:usuarios")).status_code in (302, 403)  # saiu da conta


def test_suporte_nunca_alcanca_prontuarios(client, cena):
    pront = prontuario.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=cena.maria)
    prontuario.criar_registro(cena.req(cena.dra), pront, tipo="evolucao", conteudo="Conteúdo sigiloso da sessão.")
    autorizar(client, cena)
    login_operador(client, cena)
    client.post(reverse("operador:suporte_entrar", args=[cena.consultorio.pk]))
    assert client.get(reverse("prontuario:detalhe", args=[pront.pk])).status_code == 404
    assert b"sigiloso" not in client.get(reverse("prontuario:lista")).content
    assert client.get(reverse("prontuario:excluir", args=[pront.pk])).status_code == 404


def test_autorizacao_vencida_ou_encerrada_derruba_o_acesso(client, cena):
    autorizar(client, cena)
    login_operador(client, cena)
    client.post(reverse("operador:suporte_entrar", args=[cena.consultorio.pk]))
    assert client.get(reverse("contas:usuarios")).status_code == 200
    definir_contexto(consultorio_id=cena.consultorio.pk)
    AcessoSuporte.objects.update(fim=timezone.now() - timedelta(seconds=1))
    assert client.get(reverse("contas:usuarios")).status_code in (302, 403)

    definir_contexto(consultorio_id=cena.consultorio.pk)
    AcessoSuporte.objects.update(fim=timezone.now() + timedelta(hours=1))
    client.post(reverse("operador:suporte_entrar", args=[cena.consultorio.pk]))
    assert client.get(reverse("contas:usuarios")).status_code == 200
    client.logout()
    entrar_com_2fa(client, cena.admin)
    definir_contexto(consultorio_id=cena.consultorio.pk)
    acesso = AcessoSuporte.objects.get()
    client.post(reverse("operador:suporte_revogar", args=[acesso.pk]))
    client.logout()
    login_operador(client, cena)
    resposta = client.post(reverse("operador:suporte_entrar", args=[cena.consultorio.pk]), follow=True)
    assert "ainda não autorizou".encode() in resposta.content


def test_so_o_admin_autoriza_e_so_para_equipe_da_plataforma(client, cena, criar_usuario):
    resposta = autorizar(client, cena, email="dra@a.com")  # profissional comum nao e equipe
    assert resposta.status_code == 200 and "Não encontramos".encode() in resposta.content
    assert not AcessoSuporte.objects.exists()
    entrar_com_2fa(client, cena.dra)
    assert client.get(reverse("operador:suporte_cliente")).status_code == 403


def test_duracao_invalida_e_recusada(client, cena):
    resposta = autorizar(client, cena, horas="999")
    assert resposta.status_code == 200 and not AcessoSuporte.objects.exists()


def test_admin_de_outro_consultorio_nao_encerra_o_acesso_alheio(client, cena, criar_consultorio, criar_usuario):
    autorizar(client, cena)
    acesso = AcessoSuporte.objects.get()
    outro = criar_consultorio("Outra Clínica")
    outro_admin = criar_usuario("adm@b.com", outro, Perfil.ADMIN, com_2fa=True)
    entrar_com_2fa(client, outro_admin)
    assert client.post(reverse("operador:suporte_revogar", args=[acesso.pk])).status_code == 404
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert AcessoSuporte.objects.get().vigente
