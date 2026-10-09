import time
from datetime import datetime, timedelta, timezone as tz
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from apps.agenda.models import Consulta
from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil, Profissional
from apps.core import cripto
from apps.core.tenancy import contexto, definir_contexto
from apps.prontuario import acesso, chaves, servico
from apps.prontuario.models import (
    Anexo, ChaveDados, ConsentimentoPaciente, Documento, LiberacaoLeitura, Prontuario, RegistroClinico, RegistroVersao,
)
from apps.prontuario.servico import ErroProntuario
from conftest import entrar_com_2fa

pytestmark = pytest.mark.django_db

TEXTO = "Paciente relata ansiedade intensa e insônia nas últimas semanas."


@pytest.fixture
def cena(criar_consultorio, criar_usuario, criar_paciente, fazer_request):
    class Cena:
        pass

    c = Cena()
    c.consultorio = criar_consultorio("Clínica Aurora")
    c.dra = criar_usuario("dra@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dra. Ana", com_2fa=True)
    c.dr = criar_usuario("dr@a.com", c.consultorio, Perfil.PROFISSIONAL, nome="Dr. Beto", com_2fa=True)
    c.assistente = criar_usuario("sec@a.com", c.consultorio, Perfil.ASSISTENTE, nome="Sec", com_2fa=True)
    c.assistente_sem_2fa = criar_usuario("sec2@a.com", c.consultorio, Perfil.ASSISTENTE, nome="Sec2")
    c.admin = criar_usuario("adm@a.com", c.consultorio, Perfil.ADMIN, nome="Admin", com_2fa=True)
    c.paciente = criar_paciente(c.consultorio, "Maria da Silva", email="maria@x.com")
    c.req = lambda usuario: fazer_request(usuario, c.consultorio)
    definir_contexto(consultorio_id=c.consultorio.pk)
    c.prontuario = servico.abrir_prontuario(c.req(c.dra), c.dra.profissional, paciente=c.paciente)
    c.registro = servico.criar_registro(c.req(c.dra), c.prontuario, tipo="evolucao", conteudo=TEXTO, cid="F41.1")
    return c


def login(client, usuario, c):
    entrar_com_2fa(client, usuario)
    definir_contexto(consultorio_id=c.consultorio.pk)


# --------------------------------------------------------------------------- criptografia e versoes


def test_conteudo_clinico_fica_cifrado_no_banco(cena):
    with connection.cursor() as cur:
        cur.execute("SELECT conteudo_cifrado, cid_cifrado FROM prontuario_registroversao")
        conteudo, cid = cur.fetchone()
    assert "ansiedade" not in conteudo and "insônia" not in conteudo and "F41" not in cid
    versao = cena.registro.versao_atual()
    assert servico.ler_versao(cena.req(cena.dra), versao) == (TEXTO, "F41.1")


def test_edicao_cria_versao_e_preserva_a_anterior(cena):
    request = cena.req(cena.dra)
    servico.nova_versao(request, cena.registro, conteudo=TEXTO + " Melhora parcial.", cid="F41.1")
    versoes = list(cena.registro.versoes.order_by("numero"))
    assert [v.numero for v in versoes] == [1, 2]
    assert servico.ler_versao(request, versoes[0])[0] == TEXTO
    assert servico.ler_versao(request, versoes[1])[0].endswith("Melhora parcial.")
    with pytest.raises(ErroProntuario, match="Nada foi alterado"):
        servico.nova_versao(request, cena.registro, conteudo=TEXTO + " Melhora parcial.", cid="F41.1")


def test_registro_vazio_e_recusado(cena):
    with pytest.raises(ErroProntuario):
        servico.criar_registro(cena.req(cena.dra), cena.prontuario, tipo="evolucao", conteudo="   ")


def test_banco_barra_alteracao_e_exclusao_de_versao(cena):
    versao = cena.registro.versao_atual()
    with pytest.raises(DatabaseError), transaction.atomic():
        RegistroVersao.objects.filter(pk=versao.pk).update(conteudo_cifrado="adulterado")
    with pytest.raises(DatabaseError), transaction.atomic():
        RegistroVersao.objects.filter(pk=versao.pk).delete()


def test_cada_profissional_tem_a_sua_chave_e_a_rotacao_preserva_versoes_antigas(cena):
    k_ana, k_beto = chaves.chave_ativa(cena.dra.profissional), chaves.chave_ativa(cena.dr.profissional)
    assert k_ana.pk != k_beto.pk and chaves.texto_da_chave(k_ana) != chaves.texto_da_chave(k_beto)
    assert chaves.chave_ativa(cena.dra.profissional).pk == k_ana.pk  # estavel

    nova = chaves.rotacionar(cena.dra.profissional)
    assert nova.ativa and not ChaveDados.objects.get(pk=k_ana.pk).ativa
    v2 = servico.nova_versao(cena.req(cena.dra), cena.registro, conteudo="Segunda versão.", cid="")
    assert v2.chave_id == nova.pk
    antiga = cena.registro.versoes.get(numero=1)
    assert antiga.chave_id == k_ana.pk
    assert servico.ler_versao(cena.req(cena.dra), antiga)[0] == TEXTO


def test_rotacao_da_chave_mestra(cena, settings):
    segredo_cifrado = cena.dra.segundo_fator_segredo_cifrado
    segredo = cripto.decifrar(segredo_cifrado)
    antiga = settings.MEUPSIQ_CHAVE_MESTRA
    nova = Fernet.generate_key().decode()
    settings.MEUPSIQ_CHAVE_MESTRA = f"{nova},{antiga}"
    assert servico.ler_versao(cena.req(cena.dra), cena.registro.versao_atual())[0] == TEXTO  # ainda decifra
    reescrito = cripto.recifrar(segredo_cifrado)
    settings.MEUPSIQ_CHAVE_MESTRA = nova  # a antiga saiu
    assert cripto.decifrar(reescrito) == segredo
    with pytest.raises(ValueError):
        cripto.decifrar(segredo_cifrado)  # o token antigo nao abre so com a nova


# --------------------------------------------------------------------------- acesso


def test_assistente_nao_acessa_sem_liberacao(client, cena):
    login(client, cena.assistente, cena)
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 404


def test_admin_nao_acessa_sem_liberacao(client, cena):
    login(client, cena.admin, cena)
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 404


def test_admin_le_prontuario_em_consultorio_de_demonstracao_mas_nao_escreve(client, cena, settings):
    settings.MEUPSIQ_CONSULTORIOS_SEM_2FA = [cena.consultorio.slug]
    login(client, cena.admin, cena)
    pagina = client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk]))
    assert pagina.status_code == 200
    leitura = client.get(reverse("prontuario:registro", args=[cena.prontuario.pk, cena.registro.pk]))
    assert leitura.status_code == 200 and "ansiedade intensa".encode() in leitura.content
    assert client.get(reverse("prontuario:registro_novo", args=[cena.prontuario.pk])).status_code == 404


def test_liberacao_permite_leitura_e_nao_escrita_e_e_auditada(client, cena):
    servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.assistente)
    login(client, cena.assistente, cena)
    pagina = client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk]))
    assert pagina.status_code == 200 and "no-store" in pagina["Cache-Control"]
    leitura = client.get(reverse("prontuario:registro", args=[cena.prontuario.pk, cena.registro.pk]))
    assert leitura.status_code == 200 and "ansiedade intensa".encode() in leitura.content
    assert client.get(reverse("prontuario:registro_novo", args=[cena.prontuario.pk])).status_code == 404
    assert client.post(reverse("prontuario:liberacoes", args=[cena.prontuario.pk]), {}).status_code == 404
    definir_contexto(consultorio_id=cena.consultorio.pk)
    acoes = list(Auditoria.objects.values_list("acao", flat=True))
    assert "prontuario_aberto" in acoes and "versao_lida" in acoes


def test_revogar_liberacao_fecha_o_acesso(client, cena):
    liberacao = servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.assistente)
    servico.revogar_liberacao(cena.req(cena.dra), liberacao)
    login(client, cena.assistente, cena)
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 404


def test_quem_le_prontuario_precisa_de_2fa(client, cena):
    servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.assistente_sem_2fa)
    client.post(reverse("contas:entrar"), {"email": cena.assistente_sem_2fa.email, "senha": "uma-senha-bem-longa-123"})
    resposta = client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk]))
    assert resposta.status_code == 302 and resposta.url == reverse("contas:configurar_2fa")


def test_liberacao_so_para_quem_e_do_consultorio(cena, criar_consultorio, criar_usuario):
    outro = criar_consultorio("Outra")
    estranho = criar_usuario("estranho@b.com", outro, Perfil.ASSISTENTE)
    with pytest.raises(ErroProntuario, match="não faz parte"):
        servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, estranho)


def test_prontuario_de_outro_consultorio_e_invisivel(cena, criar_consultorio):
    outro = criar_consultorio("Outra")
    with contexto(consultorio_id=outro.pk):
        assert Prontuario.objects.count() == 0 and RegistroVersao.objects.count() == 0
    assert Prontuario.objects.count() == 1


def test_abrir_prontuario_e_idempotente_e_recusa_paciente_alheio(cena, criar_consultorio, criar_paciente):
    assert servico.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=cena.paciente).pk == cena.prontuario.pk
    alheio = criar_paciente(criar_consultorio("Outra"), "Alheio")
    with pytest.raises(ErroProntuario, match="não pertence"):
        servico.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=alheio)


def test_dono_escreve_pela_interface(client, cena):
    login(client, cena.dra, cena)
    resposta = client.post(
        reverse("prontuario:registro_novo", args=[cena.prontuario.pk]),
        {"tipo": "anamnese", "conteudo": "Histórico familiar relevante.", "cid": ""},
    )
    assert resposta.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert RegistroClinico.objects.filter(prontuario=cena.prontuario).count() == 2
    editar = client.post(
        reverse("prontuario:registro_editar", args=[cena.prontuario.pk, cena.registro.pk]),
        {"conteudo": TEXTO + " Evolução.", "cid": "F41.1"},
    )
    assert editar.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    assert cena.registro.versoes.count() == 2


# --------------------------------------------------------------------------- consentimento do paciente


def test_liberar_a_outro_profissional_exige_aceite_do_paciente(client, cena):
    with pytest.raises(ErroProntuario, match="precisa autorizar"):
        servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.dr)

    mail.outbox.clear()
    (consentimento,) = servico.solicitar_consentimento(cena.req(cena.dra), cena.prontuario, cena.dr.profissional)
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["maria@x.com"] and "/termo/" in mail.outbox[0].body
    assert not consentimento.vigente
    with pytest.raises(ErroProntuario):  # pedido sem aceite ainda nao basta
        servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.dr)

    url = reverse("prontuario:termo_publico", args=[servico.token_consentimento(consentimento)])
    definir_contexto(None, None)
    assert client.get(url).status_code == 200
    assert client.post(url, {"acao": "aceitar"}, REMOTE_ADDR="203.0.113.7").status_code == 200
    definir_contexto(consultorio_id=cena.consultorio.pk)
    consentimento.refresh_from_db()
    assert consentimento.vigente and consentimento.ip_aceite == "203.0.113.7" and consentimento.termo_versao == "1.0"

    servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.dr)
    login(client, cena.dr, cena)
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 200

    client.logout()
    definir_contexto(None, None)
    client.post(url, {"acao": "revogar"}, REMOTE_ADDR="203.0.113.8")
    definir_contexto(consultorio_id=cena.consultorio.pk)
    consentimento.refresh_from_db()
    assert not consentimento.vigente and consentimento.ip_revogacao == "203.0.113.8"
    assert not LiberacaoLeitura.objects.filter(usuario=cena.dr, revogado_em__isnull=True).exists()
    login(client, cena.dr, cena)
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 404
    definir_contexto(consultorio_id=cena.consultorio.pk)
    acoes = set(Auditoria.objects.values_list("acao", flat=True))
    assert {"consentimento_aceito", "consentimento_revogado"} <= acoes


def test_consentimento_revogado_derruba_o_acesso_mesmo_com_liberacao_antiga(cena):
    (c,) = servico.solicitar_consentimento(cena.req(cena.dra), cena.prontuario, cena.dr.profissional)
    servico.aceitar_consentimento(c, "198.51.100.1")
    servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.dr)
    request = cena.req(cena.dr)
    from apps.contas.models import Vinculo

    request.vinculo = Vinculo.objects.get(usuario=cena.dr, consultorio=cena.consultorio)
    assert acesso.papel(request, cena.prontuario) == acesso.LIBERADO
    ConsentimentoPaciente.objects.filter(pk=c.pk).update(revogado_em=timezone.now())
    assert acesso.papel(request, cena.prontuario) is None


def test_pedido_exige_email_do_paciente(cena, criar_paciente):
    sem_email = criar_paciente(cena.consultorio, "Sem Email")
    prontuario = servico.abrir_prontuario(cena.req(cena.dra), cena.dra.profissional, paciente=sem_email)
    with pytest.raises(ErroProntuario, match="e-mail"):
        servico.solicitar_consentimento(cena.req(cena.dra), prontuario, cena.dr.profissional)


def test_aceite_so_uma_vez_e_token_invalido(client, cena):
    (c,) = servico.solicitar_consentimento(cena.req(cena.dra), cena.prontuario, cena.dr.profissional)
    servico.aceitar_consentimento(c, "198.51.100.1")
    with pytest.raises(ErroProntuario, match="já foi respondido"):
        servico.aceitar_consentimento(c, "198.51.100.2")
    assert client.get(reverse("prontuario:termo_publico", args=["token-falso"])).status_code == 404


# --------------------------------------------------------------------------- anexos e documentos


def arquivo(nome="exame.txt", conteudo=b"resultado do exame confidencial"):
    return SimpleUploadedFile(nome, conteudo)


def test_anexo_fica_cifrado_em_disco_e_volta_integro(cena, settings):
    anexo = servico.anexar(cena.req(cena.dra), cena.prontuario, arquivo())
    gravado = Path(settings.MEUPSIQ_ANEXOS_DIR) / anexo.caminho
    assert gravado.exists() and b"confidencial" not in gravado.read_bytes() and "exame" not in anexo.caminho
    assert servico.baixar_anexo(cena.req(cena.dra), anexo) == b"resultado do exame confidencial"


def test_anexo_recusa_extensao_e_tamanho(cena, settings):
    with pytest.raises(ErroProntuario, match="não permitido"):
        servico.anexar(cena.req(cena.dra), cena.prontuario, arquivo("virus.exe"))
    settings.MEUPSIQ_ANEXO_MAX_BYTES = 10
    with pytest.raises(ErroProntuario, match="limite"):
        servico.anexar(cena.req(cena.dra), cena.prontuario, arquivo())


def test_anexo_pela_interface_e_download(client, cena):
    login(client, cena.dra, cena)
    resposta = client.post(reverse("prontuario:anexo_enviar", args=[cena.prontuario.pk]), {"arquivo": arquivo(), "importado_historico": "on"})
    assert resposta.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    anexo = Anexo.objects.get()
    assert anexo.importado_historico
    baixado = client.get(reverse("prontuario:anexo_baixar", args=[cena.prontuario.pk, anexo.pk]))
    assert baixado.content == b"resultado do exame confidencial" and "attachment" in baixado["Content-Disposition"]


def test_documento_clinico_e_cifrado_e_so_quem_le_o_prontuario_baixa(client, cena, settings):
    documento = servico.emitir_documento_clinico(
        cena.req(cena.dra), cena.prontuario, titulo="Atestado", texto="Atesto que Maria da Silva esteve em atendimento."
    )
    gravado = (Path(settings.MEUPSIQ_ANEXOS_DIR) / documento.caminho).read_bytes()
    assert not gravado.startswith(b"%PDF") and documento.chave_id
    assert servico.baixar_documento(cena.req(cena.dra), documento).startswith(b"%PDF")

    url = reverse("prontuario:documento_baixar", args=[documento.pk])
    login(client, cena.assistente, cena)
    assert client.get(url).status_code == 404
    definir_contexto(consultorio_id=cena.consultorio.pk)
    servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.assistente)
    assert client.get(url).content.startswith(b"%PDF")


def test_marcadores_do_modelo_sao_preenchidos(cena):
    from apps.prontuario import documentos

    consulta = Consulta.objects.create(
        consultorio=cena.consultorio, profissional=cena.dra.profissional, paciente=cena.paciente,
        inicio=datetime(2026, 3, 10, 17, 0, tzinfo=tz.utc), fim=datetime(2026, 3, 10, 17, 50, tzinfo=tz.utc),
    )
    dados = documentos.valores(paciente=cena.paciente, profissional=cena.dra.profissional, consultorio=cena.consultorio, consulta=consulta)
    texto = documentos.preencher("{{paciente_nome}} em {{data_consulta}} das {{hora_inicio}} às {{hora_fim}} {{inexistente}}", dados)
    assert texto == "Maria da Silva em 10/03/2026 das 14:00 às 14:50 {{inexistente}}"  # fuso de Sao Paulo


def test_assistente_emite_declaracao_de_comparecimento_sem_ler_prontuario(client, cena):
    consulta = Consulta.objects.create(
        consultorio=cena.consultorio, profissional=cena.dra.profissional, paciente=cena.paciente,
        inicio=timezone.now() - timedelta(days=1), fim=timezone.now() - timedelta(days=1) + timedelta(minutes=50),
        status=Consulta.Status.AGENDADA,
    )
    with pytest.raises(ErroProntuario, match="realizadas"):
        servico.emitir_comparecimento(cena.req(cena.assistente), consulta)
    consulta.status = Consulta.Status.REALIZADA
    consulta.save()

    login(client, cena.assistente, cena)
    resposta = client.post(reverse("prontuario:comparecimento", args=[consulta.pk]))
    assert resposta.status_code == 302
    definir_contexto(consultorio_id=cena.consultorio.pk)
    documento = Documento.objects.get()
    assert documento.chave_id is None and documento.prontuario_id is None
    assert client.get(resposta.url).content.startswith(b"%PDF")
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 404  # segue sem acesso


# --------------------------------------------------------------------------- exclusao antecipada, delegacao, retencao


def test_exclusao_antecipada_exige_confirmacao_e_apaga_tudo(cena, settings):
    anexo = servico.anexar(cena.req(cena.dra), cena.prontuario, arquivo())
    arquivo_em_disco = Path(settings.MEUPSIQ_ANEXOS_DIR) / anexo.caminho
    request = cena.req(cena.dra)
    with pytest.raises(ErroProntuario, match="digite o nome"):
        servico.excluir_antecipadamente(request, cena.prontuario, motivo="Pedido da paciente", confirmacao_nome="Outra Pessoa")
    with pytest.raises(ErroProntuario, match="motivo"):
        servico.excluir_antecipadamente(request, cena.prontuario, motivo=" ", confirmacao_nome="maria da silva")
    assert RegistroVersao.objects.exists() and arquivo_em_disco.exists()

    servico.excluir_antecipadamente(request, cena.prontuario, motivo="Pedido da paciente", confirmacao_nome="maria da silva")
    cena.prontuario.refresh_from_db()
    assert cena.prontuario.excluido_em and cena.prontuario.exclusao_motivo == "Pedido da paciente"
    assert not RegistroClinico.objects.exists() and not RegistroVersao.objects.exists() and not Anexo.objects.exists()
    assert not arquivo_em_disco.exists()
    assert Auditoria.objects.filter(acao="prontuario_exclusao_antecipada").exists()

    # a janela de exclusao fechou: versoes novas continuam protegidas
    novo = servico.abrir_prontuario(request, cena.dra.profissional, paciente=cena.paciente)
    registro = servico.criar_registro(request, novo, tipo="evolucao", conteudo="Novo começo.")
    with pytest.raises(DatabaseError), transaction.atomic():
        RegistroVersao.objects.filter(registro=registro).delete()


def test_exclusao_so_pelo_dono(client, cena):
    login(client, cena.assistente, cena)
    assert client.get(reverse("prontuario:excluir", args=[cena.prontuario.pk])).status_code == 404


def test_delegacao_troca_o_dono_sem_o_admin_ler_o_conteudo(client, cena):
    login(client, cena.admin, cena)
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 404  # admin nao le
    assert client.get(reverse("prontuario:delegar")).status_code == 200
    client.logout()
    definir_contexto(consultorio_id=cena.consultorio.pk)

    servico.conceder_liberacao(cena.req(cena.dra), cena.prontuario, cena.assistente)
    servico.delegar(cena.req(cena.admin), cena.prontuario, cena.dr.profissional, "Saída da Dra. Ana")
    cena.prontuario.refresh_from_db()
    assert cena.prontuario.profissional_id == cena.dr.profissional.pk
    assert cena.prontuario.delegacoes.get().de_id == cena.dra.profissional.pk
    assert not cena.prontuario.liberacoes.filter(revogado_em__isnull=True).exists()
    assert Auditoria.objects.filter(acao="prontuario_delegado").exists()

    # o novo dono le versoes cifradas com a chave do antigo; o antigo perde o acesso
    login(client, cena.dr, cena)
    leitura = client.get(reverse("prontuario:registro", args=[cena.prontuario.pk, cena.registro.pk]))
    assert leitura.status_code == 200 and "ansiedade intensa".encode() in leitura.content
    client.logout()
    login(client, cena.dra, cena)
    assert client.get(reverse("prontuario:detalhe", args=[cena.prontuario.pk])).status_code == 404


def test_delegacao_so_pelo_admin_e_recusa_destino_com_prontuario(client, cena):
    login(client, cena.dra, cena)
    assert client.get(reverse("prontuario:delegar")).status_code == 403
    definir_contexto(consultorio_id=cena.consultorio.pk)
    servico.abrir_prontuario(cena.req(cena.dr), cena.dr.profissional, paciente=cena.paciente)
    with pytest.raises(ErroProntuario, match="já tem um prontuário"):
        servico.delegar(cena.req(cena.admin), cena.prontuario, cena.dr.profissional)


def test_prazo_de_guarda_por_tipo_de_profissional(cena):
    ultimo = datetime(2026, 2, 28, tzinfo=tz.utc)
    psicologo = Prontuario(profissional=Profissional(tipo="psicologo"), ultimo_registro_em=ultimo)
    medico = Prontuario(profissional=Profissional(tipo="medico"), ultimo_registro_em=ultimo)
    assert psicologo.prazo_guarda_ate.year == 2031 and medico.prazo_guarda_ate.year == 2046
    bissexto = Prontuario(profissional=Profissional(tipo="psicologo"), ultimo_registro_em=datetime(2024, 2, 29, tzinfo=tz.utc))
    assert (bissexto.prazo_guarda_ate.month, bissexto.prazo_guarda_ate.day) == (2, 28)


# --------------------------------------------------------------------------- sessao


def test_sessao_expira_por_inatividade_na_area_de_prontuario(client, cena):
    login(client, cena.dra, cena)
    assert client.get(reverse("prontuario:lista")).status_code == 200
    sessao = client.session
    sessao["ultima_atividade"] = time.time() - 3600
    sessao.save()
    resposta = client.get(reverse("prontuario:lista"))
    assert resposta.status_code == 302 and resposta.url == reverse("contas:entrar")
    assert client.get(reverse("painel")).status_code == 302  # a sessao foi encerrada


def test_inatividade_longa_nao_derruba_fora_da_area_de_prontuario(client, cena):
    login(client, cena.dra, cena)
    client.get(reverse("painel"))
    sessao = client.session
    sessao["ultima_atividade"] = time.time() - 3600
    sessao.save()
    assert client.get(reverse("painel")).status_code == 200
