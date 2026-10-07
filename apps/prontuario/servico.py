"""Regras do prontuario. Conteudo clinico so passa por aqui: cifrado ao gravar, decifrado ao ler, sempre auditado.
Nada de conteudo clinico em logs, auditoria ou mensagens."""

import hashlib
from datetime import timedelta
import os
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import connection, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.auditoria import servico as auditoria
from apps.core import cripto
from apps.contas.models import Perfil, Usuario, Vinculo
from apps.pacientes.models import Paciente
from apps.plataforma.models import Contrato

from . import chaves, documentos
from .armazenamento import armazenamento
from .models import (
    Anexo, ConsentimentoPaciente, Delegacao, Documento, LiberacaoLeitura, ModeloDocumento, Prontuario,
    RegistroClinico, RegistroVersao,
)
from .termos import TERMO_ATUAL

SAL_CONSENTIMENTO = "psiq.consentimento"
VALIDADE_TOKEN_SEGUNDOS = 60 * 60 * 24 * 365
PRAZO_PARA_ACEITAR_DIAS = 30


class ErroProntuario(Exception):
    """Regra violada; a mensagem pode ser mostrada ao usuario."""


# --------------------------------------------------------------------------- prontuario


@transaction.atomic
def abrir_prontuario(request, profissional, paciente=None, grupo=None):
    if (paciente is None) == (grupo is None):
        raise ErroProntuario("Informe um paciente ou um grupo.")
    alvo = paciente or grupo
    consultorio = request.consultorio
    if alvo.consultorio_id != consultorio.pk:
        raise ErroProntuario("Paciente ou grupo não pertence a este consultório.")
    if paciente is not None and paciente.mesclado_em:
        raise ErroProntuario("Este cadastro foi mesclado com outro. Use o cadastro principal.")

    filtro = {"paciente": paciente} if paciente else {"grupo": grupo}
    existente = Prontuario.objects.filter(profissional=profissional, excluido_em__isnull=True, **filtro).first()
    if existente:
        return existente
    contrato = (
        Contrato.objects.filter(consultorio=consultorio, status=Contrato.Status.ATIVO)
        .filter(Q(tipo=Contrato.Tipo.CONSULTORIO) | Q(profissional=profissional))
        .first()
    )
    prontuario = Prontuario.objects.create(
        consultorio=consultorio, profissional=profissional, contrato=contrato,
        tipo=Prontuario.Tipo.CONJUNTO if grupo else Prontuario.Tipo.INDIVIDUAL, **filtro,
    )
    auditoria.registrar(request, "prontuario_criado", "prontuario", prontuario.pk)
    return prontuario


# --------------------------------------------------------------------------- registros e versoes


def _gravar_versao(registro, numero, conteudo, cid, profissional, usuario):
    chave = chaves.chave_ativa(profissional)
    return RegistroVersao.objects.create(
        consultorio_id=registro.consultorio_id,
        registro=registro,
        numero=numero,
        conteudo_cifrado=chaves.cifrar_texto(chave, conteudo),
        cid_cifrado=chaves.cifrar_texto(chave, cid) if cid else "",
        chave=chave,
        autor_id=usuario.pk,
    )


@transaction.atomic
def criar_registro(request, prontuario, *, tipo, conteudo, cid="", consulta=None):
    conteudo = conteudo.strip()
    if not conteudo:
        raise ErroProntuario("O registro não pode ficar vazio.")
    registro = RegistroClinico.objects.create(
        consultorio=prontuario.consultorio, prontuario=prontuario, tipo=tipo, consulta=consulta
    )
    _gravar_versao(registro, 1, conteudo, cid.strip(), prontuario.profissional, request.user)
    Prontuario.objects.filter(pk=prontuario.pk).update(ultimo_registro_em=timezone.now())
    auditoria.registrar(request, "registro_criado", "registro", registro.pk, tipo=tipo)
    return registro


def ler_versao(request, versao, *, auditar=True):
    """Retorna (conteudo, cid) decifrados com a chave com que a versao foi gravada."""
    conteudo = chaves.decifrar_texto(versao.chave, versao.conteudo_cifrado)
    cid = chaves.decifrar_texto(versao.chave, versao.cid_cifrado) if versao.cid_cifrado else ""
    if auditar:
        auditoria.registrar(request, "versao_lida", "registro", versao.registro_id, numero=versao.numero)
    return conteudo, cid


@transaction.atomic
def nova_versao(request, registro, *, conteudo, cid=""):
    conteudo, cid = conteudo.strip(), cid.strip()
    if not conteudo:
        raise ErroProntuario("O registro não pode ficar vazio.")
    registro = RegistroClinico.objects.select_for_update().select_related("prontuario__profissional").get(pk=registro.pk)
    atual = registro.versao_atual()
    if atual is not None and ler_versao(request, atual, auditar=False) == (conteudo, cid):
        raise ErroProntuario("Nada foi alterado.")
    numero = (registro.versoes.aggregate(m=Max("numero"))["m"] or 0) + 1
    versao = _gravar_versao(registro, numero, conteudo, cid, registro.prontuario.profissional, request.user)
    Prontuario.objects.filter(pk=registro.prontuario_id).update(ultimo_registro_em=timezone.now())
    auditoria.registrar(request, "registro_versao_criada", "registro", registro.pk, numero=numero)
    return versao


# --------------------------------------------------------------------------- anexos


def anexar(request, prontuario, arquivo, *, importado_historico=False):
    nome = os.path.basename(arquivo.name or "arquivo")[:200]
    extensao = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if extensao not in settings.PSIQ_ANEXO_EXTENSOES:
        raise ErroProntuario("Tipo de arquivo não permitido. Use: " + ", ".join(settings.PSIQ_ANEXO_EXTENSOES) + ".")
    if arquivo.size > settings.PSIQ_ANEXO_MAX_BYTES:
        raise ErroProntuario(f"O arquivo passa do limite de {settings.PSIQ_ANEXO_MAX_BYTES // (1024 * 1024)} MB.")
    dados = arquivo.read()
    chave = chaves.chave_ativa(prontuario.profissional)
    caminho = armazenamento().salvar(chaves.cifrar_arquivo(chave, dados))
    try:
        anexo = Anexo.objects.create(
            consultorio=prontuario.consultorio, prontuario=prontuario, nome=nome, caminho=caminho, tamanho=len(dados),
            hash_sha256=hashlib.sha256(dados).hexdigest(), importado_historico=importado_historico, chave=chave,
            enviado_por_id=request.user.pk,
        )
    except Exception:
        armazenamento().apagar(caminho)
        raise
    auditoria.registrar(request, "anexo_enviado", "anexo", anexo.pk)
    return anexo


def baixar_anexo(request, anexo):
    dados = chaves.decifrar_arquivo(anexo.chave, armazenamento().ler(anexo.caminho))
    if hashlib.sha256(dados).hexdigest() != anexo.hash_sha256:
        raise ErroProntuario("O arquivo não passou na verificação de integridade.")
    auditoria.registrar(request, "anexo_baixado", "anexo", anexo.pk)
    return dados


# --------------------------------------------------------------------------- documentos


def garantir_modelos_padrao(consultorio):
    existentes = set(ModeloDocumento.objects.filter(consultorio=consultorio).values_list("tipo", flat=True))
    for tipo, titulo, texto in documentos.MODELOS_PADRAO:
        if tipo not in existentes:
            ModeloDocumento.objects.create(consultorio=consultorio, tipo=tipo, titulo=titulo, texto_base=texto)
    return ModeloDocumento.objects.filter(consultorio=consultorio)


def _emitir(request, *, paciente, profissional, titulo, texto, modelo, prontuario, clinico):
    consultorio = request.consultorio
    fuso = ZoneInfo(consultorio.fuso)
    pdf = documentos.gerar_pdf(
        titulo=titulo, texto=texto, consultorio_nome=consultorio.nome,
        assinatura=f"{profissional.usuario.nome} - {profissional.conselho} {profissional.numero}/{profissional.uf}",
        local_data=f"{timezone.localtime(timezone.now(), fuso):%d/%m/%Y}",
    )
    chave = chaves.chave_ativa(profissional) if clinico else None
    conteudo = chaves.cifrar_arquivo(chave, pdf) if chave else cripto.cifrar_bytes(pdf)
    caminho = armazenamento().salvar(conteudo)
    documento = Documento.objects.create(
        consultorio=consultorio, paciente=paciente, prontuario=prontuario, modelo=modelo, titulo=titulo[:160],
        caminho=caminho, hash_sha256=hashlib.sha256(pdf).hexdigest(), chave=chave, autor_id=request.user.pk,
    )
    auditoria.registrar(request, "documento_emitido", "documento", documento.pk, clinico=clinico)
    return documento


@transaction.atomic
def emitir_documento_clinico(request, prontuario, *, titulo, texto, modelo=None):
    """Declaracao, atestado ou relatorio emitido pelo profissional dono; fica gravado no prontuario, cifrado."""
    if prontuario.paciente_id is None:
        raise ErroProntuario("Emita o documento a partir do prontuário individual do paciente.")
    if not texto.strip():
        raise ErroProntuario("O documento não pode ficar vazio.")
    return _emitir(
        request, paciente=prontuario.paciente, profissional=prontuario.profissional, titulo=titulo, texto=texto,
        modelo=modelo, prontuario=prontuario, clinico=True,
    )


@transaction.atomic
def emitir_comparecimento(request, consulta):
    """Declaracao de comparecimento: usa so dados da agenda, por isso a assistente pode emitir. Sem conteudo clinico."""
    from apps.agenda.models import Consulta

    if consulta.status != Consulta.Status.REALIZADA:
        raise ErroProntuario("A declaração só pode ser emitida para consultas realizadas.")
    if consulta.paciente_id is None:
        raise ErroProntuario("Emita a declaração por paciente, a partir de uma consulta individual.")
    modelo = garantir_modelos_padrao(request.consultorio).get(tipo=ModeloDocumento.Tipo.COMPARECIMENTO)
    dados = documentos.valores(
        paciente=consulta.paciente, profissional=consulta.profissional, consultorio=request.consultorio, consulta=consulta
    )
    return _emitir(
        request, paciente=consulta.paciente, profissional=consulta.profissional, titulo=modelo.titulo,
        texto=documentos.preencher(modelo.texto_base, dados), modelo=modelo, prontuario=None, clinico=False,
    )


def baixar_documento(request, documento):

    bruto = armazenamento().ler(documento.caminho)
    pdf = chaves.decifrar_arquivo(documento.chave, bruto) if documento.chave_id else cripto.decifrar_bytes(bruto)
    auditoria.registrar(request, "documento_baixado", "documento", documento.pk)
    return pdf


# --------------------------------------------------------------------------- liberacao e consentimento


def conceder_liberacao(request, prontuario, usuario):
    from .acesso import consentimento_vigente

    vinculo = Vinculo.objects.filter(consultorio=request.consultorio, usuario=usuario, ativo=True).first()
    if vinculo is None:
        raise ErroProntuario("Esse usuário não faz parte do consultório.")
    if usuario.pk == request.user.pk:
        raise ErroProntuario("Você já é o dono deste prontuário.")
    if vinculo.perfil == Perfil.PROFISSIONAL:
        if not consentimento_vigente(prontuario, usuario.profissional):
            raise ErroProntuario("Para liberar a outro profissional, o paciente precisa autorizar antes (aceite eletrônico).")
    liberacao, criada = LiberacaoLeitura.objects.get_or_create(
        consultorio=prontuario.consultorio, prontuario=prontuario, usuario=usuario, revogado_em=None,
        defaults={"concedido_por_id": request.user.pk},
    )
    if criada:
        auditoria.registrar(request, "liberacao_concedida", "prontuario", prontuario.pk, usuario_liberado=str(usuario.pk))
    return liberacao


def revogar_liberacao(request, liberacao):
    liberacao.revogado_em = timezone.now()
    liberacao.save(update_fields=["revogado_em", "atualizado_em"])
    auditoria.registrar(request, "liberacao_revogada", "prontuario", liberacao.prontuario_id, usuario_liberado=str(liberacao.usuario_id))


def _emails_do_paciente(paciente):
    emails = [] if paciente.menor_de_idade else [paciente.email]
    emails += [r.email for r in paciente.responsaveis.filter(recebe_avisos=True)]
    return [e for e in dict.fromkeys(emails) if e]


def token_consentimento(consentimento) -> str:
    return signing.dumps({"n": str(consentimento.pk), "k": str(consentimento.consultorio_id)}, salt=SAL_CONSENTIMENTO)


def ler_token_consentimento(token: str) -> dict:
    return signing.loads(token, salt=SAL_CONSENTIMENTO, max_age=VALIDADE_TOKEN_SEGUNDOS)


def solicitar_consentimento(request, prontuario, profissional_destino):
    """Pede ao paciente (ou a cada participante do conjunto) o aceite eletronico para outro profissional ler."""
    from .acesso import pacientes_do_prontuario

    if profissional_destino.pk == prontuario.profissional_id:
        raise ErroProntuario("O destino é o próprio dono do prontuário.")
    pacientes = pacientes_do_prontuario(prontuario)
    sem_email = [p.nome for p in pacientes if not _emails_do_paciente(p)]
    if sem_email:
        raise ErroProntuario("Cadastre o e-mail do paciente (ou do responsável que recebe avisos) para enviar o pedido.")
    criados = []
    with transaction.atomic():
        for paciente in pacientes:
            pendente = ConsentimentoPaciente.objects.filter(
                paciente=paciente, profissional_origem=prontuario.profissional, profissional_destino=profissional_destino,
                revogado_em__isnull=True,
            ).first()
            if pendente is None:
                pendente = ConsentimentoPaciente.objects.create(
                    consultorio=prontuario.consultorio, paciente=paciente, profissional_origem=prontuario.profissional,
                    profissional_destino=profissional_destino,
                )
            criados.append(pendente)
        auditoria.registrar(request, "consentimento_solicitado", "prontuario", prontuario.pk, destino=str(profissional_destino.pk))
    for consentimento in criados:
        if consentimento.vigente:
            continue
        link = f"{settings.PSIQ_URL_BASE.rstrip('/')}/termo/{token_consentimento(consentimento)}/"
        corpo = (
            "Olá.\n\nHá um pedido de autorização para compartilhar informações do seu acompanhamento com outro "
            f"profissional.\nLeia e responda por este link: {link}\n\n{prontuario.consultorio.nome}"
        )
        for email in _emails_do_paciente(consentimento.paciente):
            send_mail(f"Pedido de autorização - {prontuario.consultorio.nome}", corpo, None, [email], fail_silently=True)
    return criados


def aceitar_consentimento(consentimento, ip):
    if consentimento.revogado_em or consentimento.aceito_em:
        raise ErroProntuario("Este pedido já foi respondido.")
    if timezone.now() - consentimento.criado_em > timedelta(days=PRAZO_PARA_ACEITAR_DIAS):
        raise ErroProntuario("Este pedido venceu. Peça um novo ao profissional.")
    consentimento.aceito_em, consentimento.ip_aceite, consentimento.termo_versao = timezone.now(), ip, TERMO_ATUAL
    consentimento.save()
    _auditar_publico("consentimento_aceito", consentimento, ip)


@transaction.atomic
def revogar_consentimento(consentimento, ip):
    if consentimento.revogado_em:
        raise ErroProntuario("Esta autorização já foi revogada.")
    consentimento.revogado_em, consentimento.ip_revogacao = timezone.now(), ip
    consentimento.save()
    destino_usuario = consentimento.profissional_destino.usuario_id
    LiberacaoLeitura.objects.filter(
        prontuario__profissional=consentimento.profissional_origem, usuario_id=destino_usuario, revogado_em__isnull=True
    ).update(revogado_em=timezone.now())
    _auditar_publico("consentimento_revogado", consentimento, ip)


def _auditar_publico(acao, consentimento, ip):
    from apps.auditoria.models import Auditoria

    Auditoria.objects.create(
        consultorio_id=consentimento.consultorio_id, acao=acao, objeto="consentimento", objeto_id=str(consentimento.pk),
        ip=ip, detalhe={"termo_versao": consentimento.termo_versao},
    )


# --------------------------------------------------------------------------- exclusao antecipada e delegacao


def excluir_antecipadamente(request, prontuario, *, motivo, confirmacao_nome):
    """Pedido do paciente confirmado pelo profissional dono. Apaga o conteudo; mantem um registro minimo do fato."""
    alvo_nome = prontuario.paciente.nome if prontuario.paciente_id else prontuario.grupo.nome
    if confirmacao_nome.strip().casefold() != alvo_nome.strip().casefold():
        raise ErroProntuario("Para confirmar, digite o nome exatamente como aparece no cadastro.")
    if not motivo.strip():
        raise ErroProntuario("Registre o motivo (pedido do paciente).")

    caminhos = [a.caminho for a in prontuario.anexos.all()] + [d.caminho for d in prontuario.documentos.all()]
    with transaction.atomic():
        with connection.cursor() as cur:  # libera DELETE de versoes so nesta transacao
            cur.execute("SELECT set_config('app.exclusao_autorizada', 'on', true)")
        prontuario.registros.all().delete()  # DELETE de versoes liberado pelo flag acima
        prontuario.anexos.all().delete()
        prontuario.documentos.all().delete()
        prontuario.liberacoes.all().delete()
        with connection.cursor() as cur:  # fecha a janela: o flag nao pode sobreviver a esta operacao
            cur.execute("SELECT set_config('app.exclusao_autorizada', '', true)")
        prontuario.excluido_em = timezone.now()
        prontuario.excluido_por_id = request.user.pk
        prontuario.exclusao_motivo = motivo.strip()[:300]
        prontuario.save()
        auditoria.registrar(request, "prontuario_exclusao_antecipada", "prontuario", prontuario.pk)
    for caminho in caminhos:
        armazenamento().apagar(caminho)


@transaction.atomic
def delegar(request, prontuario, novo_profissional, motivo=""):
    """Troca o dono (saida de profissional). O admin nao le o conteudo; as versoes seguem decifraveis pela chave de origem."""
    from .acesso import pacientes_do_prontuario  # noqa: F401  (mantem a regra de mesmo consultorio visivel)
    from apps.agenda.servico import profissionais_do_consultorio

    if novo_profissional.pk == prontuario.profissional_id:
        raise ErroProntuario("O prontuário já pertence a este profissional.")
    if not profissionais_do_consultorio(request.consultorio).filter(pk=novo_profissional.pk).exists():
        raise ErroProntuario("O profissional de destino não atende neste consultório.")
    if prontuario.paciente_id and Prontuario.objects.filter(
        paciente=prontuario.paciente, profissional=novo_profissional, excluido_em__isnull=True
    ).exists():
        raise ErroProntuario("O profissional de destino já tem um prontuário deste paciente.")
    antigo = prontuario.profissional
    Delegacao.objects.create(
        consultorio=prontuario.consultorio, prontuario=prontuario, de=antigo, para=novo_profissional,
        feita_por_id=request.user.pk, motivo=motivo[:300],
    )
    prontuario.profissional = novo_profissional
    prontuario.save(update_fields=["profissional", "atualizado_em"])
    prontuario.liberacoes.filter(revogado_em__isnull=True).update(revogado_em=timezone.now())
    auditoria.registrar(request, "prontuario_delegado", "prontuario", prontuario.pk, de=str(antigo.pk), para=str(novo_profissional.pk))
