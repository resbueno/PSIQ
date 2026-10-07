"""Regras financeiras: lancamento, pagamento, convenio e glosa, recibo, repasse e cobranca recorrente.
Dinheiro em Decimal, arredondado a centavo (ROUND_HALF_UP)."""

import hashlib
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.auditoria import servico as auditoria
from apps.core import cripto
from apps.core.validadores import formatar_cpf
from apps.plataforma.models import Consultorio
from apps.prontuario import documentos
from apps.prontuario.armazenamento import armazenamento

from .models import (
    AtendimentoConvenio, CobrancaRecorrente, Convenio, Lancamento, Recibo, RegraRepasse, Repasse, TabelaValor, TipoValor,
)

CENTAVO = Decimal("0.01")


class ErroFinanceiro(Exception):
    """Regra violada; a mensagem pode ser mostrada ao usuario."""


def arredondar(valor) -> Decimal:
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def primeiro_dia_do_mes(dia: date) -> date:
    return dia.replace(day=1)


# --------------------------------------------------------------------------- valores e lancamentos


def convenio_do_paciente(consultorio, paciente):
    """O cadastro do paciente guarda o nome do convenio em texto; casa com um convenio ativo do consultorio."""
    nome = (paciente.convenio or "").strip()
    if not nome:
        return None
    return Convenio.objects.filter(consultorio=consultorio, ativo=True, operadora__iexact=nome).first()


def valor_da_sessao(consultorio, profissional, convenio, primeira: bool):
    """Busca na tabela: primeiro o valor de 'primeira consulta' (se for o caso), depois o de 'consulta'."""
    tipos = ([TipoValor.PRIMEIRA] if primeira else []) + [TipoValor.CONSULTA]
    tabela = TabelaValor.objects.filter(consultorio=consultorio, profissional=profissional, convenio=convenio)
    for tipo in tipos:
        encontrado = tabela.filter(tipo=tipo).first()
        if encontrado:
            return encontrado.valor
    return None


def _data_local(consulta):
    return timezone.localtime(consulta.inicio, ZoneInfo(consulta.consultorio.fuso)).date()


@transaction.atomic
def gerar_lancamento(request, consulta, *, valor=None, origem=Lancamento.Origem.CONSULTA):
    """Cria a cobranca de uma consulta (idempotente por consulta e origem). Retorna None se nao ha valor configurado."""
    if consulta.paciente_id is None:
        raise ErroFinanceiro("Lance a cobrança por paciente (consultas em grupo ainda são lançadas manualmente).")
    existente = Lancamento.objects.filter(consulta=consulta, origem=origem).first()
    if existente:
        return existente

    paciente, consultorio = consulta.paciente, consulta.consultorio
    convenio = convenio_do_paciente(consultorio, paciente) if origem == Lancamento.Origem.CONSULTA else None
    if valor is None:
        valor = valor_da_sessao(consultorio, consulta.profissional, convenio, consulta.primeira_consulta)
        if valor is None:  # sem valor configurado nao inventa cobranca
            return None
    pagador = getattr(paciente, "pagador", None)
    lancamento = Lancamento.objects.create(
        consultorio=consultorio, consulta=consulta, paciente=paciente, pagador=pagador, profissional=consulta.profissional,
        convenio=convenio, tipo=Lancamento.Tipo.CONVENIO if convenio else Lancamento.Tipo.PARTICULAR, origem=origem,
        primeira_consulta=consulta.primeira_consulta, valor=arredondar(valor), vencimento=_data_local(consulta),
        descricao="Cancelamento tardio" if origem == Lancamento.Origem.FALTA_TARDIA else "",
    )
    if convenio:
        AtendimentoConvenio.objects.create(
            consultorio=consultorio, lancamento=lancamento, carteirinha=paciente.carteirinha,
        )
    auditoria.registrar(request, "lancamento_criado", "lancamento", lancamento.pk, consultorio_id=consultorio.pk, tipo=lancamento.tipo)
    return lancamento


def gerar_lancamento_falta_tardia(request, consulta):
    """Chamado quando a equipe registra cancelamento tardio e o consultorio optou por cobrar."""
    if not consulta.consultorio.cobra_falta_tardia or consulta.paciente_id is None:
        return None
    return gerar_lancamento(request, consulta, origem=Lancamento.Origem.FALTA_TARDIA)


# --------------------------------------------------------------------------- pagamento e repasse


def regra_de_repasse(consultorio, profissional, lancamento):
    regras = {r.aplicacao: r for r in RegraRepasse.objects.filter(consultorio=consultorio, profissional=profissional)}
    candidatas = []
    if lancamento.primeira_consulta:
        candidatas.append(RegraRepasse.Aplicacao.PRIMEIRA)
    candidatas += [lancamento.tipo, RegraRepasse.Aplicacao.PADRAO]
    for chave in candidatas:
        if chave in regras:
            return regras[chave]
    return None


def calcular_repasse(regra, base: Decimal) -> Decimal:
    if regra.percentual is not None:
        return arredondar(base * regra.percentual / Decimal(100))
    return min(arredondar(regra.valor_fixo), arredondar(base))  # valor fixo nunca passa do que entrou


def _gerar_repasse(request, lancamento, base, quando):
    """O repasse nasce quando o dinheiro entra. Aluguel de sala nao gera repasse."""
    if lancamento.tipo == Lancamento.Tipo.ALUGUEL or base <= 0:
        return None
    regra = regra_de_repasse(lancamento.consultorio, lancamento.profissional, lancamento)
    if regra is None:
        return None
    repasse = Repasse.objects.create(
        consultorio=lancamento.consultorio, profissional=lancamento.profissional, lancamento=lancamento,
        competencia=primeiro_dia_do_mes(quando.date()), base=base, valor=calcular_repasse(regra, base),
    )
    auditoria.registrar(request, "repasse_gerado", "repasse", repasse.pk, consultorio_id=lancamento.consultorio_id)
    return repasse


@transaction.atomic
def registrar_pagamento(request, lancamento, *, forma, pago_em=None):
    if lancamento.tipo == Lancamento.Tipo.CONVENIO:
        raise ErroFinanceiro("O pagamento de convênio é registrado pelo retorno da operadora.")
    if lancamento.status != Lancamento.Status.PENDENTE:
        raise ErroFinanceiro("Este lançamento não está pendente.")
    pago_em = pago_em or timezone.now()
    lancamento.status, lancamento.forma_pagamento, lancamento.pago_em = Lancamento.Status.PAGO, forma, pago_em
    lancamento.save(update_fields=["status", "forma_pagamento", "pago_em", "atualizado_em"])
    _gerar_repasse(request, lancamento, lancamento.valor, pago_em)
    auditoria.registrar(request, "pagamento_registrado", "lancamento", lancamento.pk, consultorio_id=lancamento.consultorio_id)


@transaction.atomic
def desfazer_pagamento(request, lancamento):
    if lancamento.status != Lancamento.Status.PAGO or lancamento.tipo == Lancamento.Tipo.CONVENIO:
        raise ErroFinanceiro("Só é possível desfazer o pagamento de um lançamento particular pago.")
    repasse = Repasse.objects.filter(lancamento=lancamento).first()
    if repasse and repasse.status == Repasse.Status.PAGO:
        raise ErroFinanceiro("O repasse deste lançamento já foi pago. Desfaça-o com o administrador antes.")
    if repasse:
        repasse.delete()
    if Recibo.objects.filter(lancamento=lancamento).exists():
        raise ErroFinanceiro("Já existe recibo emitido para este pagamento.")
    lancamento.status, lancamento.forma_pagamento, lancamento.pago_em = Lancamento.Status.PENDENTE, "", None
    lancamento.save(update_fields=["status", "forma_pagamento", "pago_em", "atualizado_em"])
    auditoria.registrar(request, "pagamento_desfeito", "lancamento", lancamento.pk, consultorio_id=lancamento.consultorio_id)


def cancelar_lancamento(request, lancamento):
    if lancamento.status != Lancamento.Status.PENDENTE:
        raise ErroFinanceiro("Só é possível cancelar lançamentos pendentes.")
    lancamento.status = Lancamento.Status.CANCELADO
    lancamento.save(update_fields=["status", "atualizado_em"])
    auditoria.registrar(request, "lancamento_cancelado", "lancamento", lancamento.pk, consultorio_id=lancamento.consultorio_id)


@transaction.atomic
def marcar_repasse_pago(request, repasse):
    if repasse.status == Repasse.Status.PAGO:
        raise ErroFinanceiro("Este repasse já está pago.")
    repasse.status, repasse.pago_em, repasse.pago_por_id = Repasse.Status.PAGO, timezone.now(), request.user.pk
    repasse.save(update_fields=["status", "pago_em", "pago_por_id", "atualizado_em"])
    auditoria.registrar(request, "repasse_pago", "repasse", repasse.pk, consultorio_id=repasse.consultorio_id)


# --------------------------------------------------------------------------- convenio


@transaction.atomic
def faturar(request, atendimentos, demonstrativo=""):
    """Marca atendimentos realizados como faturados (entrega do lote a operadora, feita fora do PSIQ)."""
    agora = timezone.now()
    for atendimento in atendimentos:
        if atendimento.status != AtendimentoConvenio.Status.REALIZADO:
            raise ErroFinanceiro("Só atendimentos realizados podem ser faturados.")
        atendimento.status, atendimento.faturado_em, atendimento.demonstrativo = (
            AtendimentoConvenio.Status.FATURADO, agora, demonstrativo[:60],
        )
        atendimento.save(update_fields=["status", "faturado_em", "demonstrativo", "atualizado_em"])
    auditoria.registrar(request, "convenio_faturado", "lancamento", "", quantidade=len(atendimentos))


@transaction.atomic
def registrar_retorno(request, atendimento, *, valor_recebido, motivo="", demonstrativo=""):
    """Retorno da operadora. Recebeu tudo: pago. Recebeu parte: pago com glosa (o repasse usa o que entrou).
    Nao recebeu nada: glosado, sem repasse."""
    if atendimento.status not in (AtendimentoConvenio.Status.FATURADO, AtendimentoConvenio.Status.GLOSADO):
        raise ErroFinanceiro("Registre o retorno de atendimentos faturados.")
    lancamento = atendimento.lancamento
    recebido = arredondar(valor_recebido)
    if recebido < 0 or recebido > lancamento.valor:
        raise ErroFinanceiro("O valor recebido deve ficar entre zero e o valor do atendimento.")
    glosa = lancamento.valor - recebido
    if glosa > 0 and not motivo.strip():
        raise ErroFinanceiro("Informe o motivo da glosa.")

    agora = timezone.now()
    atendimento.valor_recebido, atendimento.valor_glosado, atendimento.motivo_glosa = recebido, glosa, motivo.strip()[:200]
    atendimento.retorno_em = agora
    if demonstrativo:
        atendimento.demonstrativo = demonstrativo[:60]
    atendimento.status = AtendimentoConvenio.Status.GLOSADO if recebido == 0 else AtendimentoConvenio.Status.PAGO
    atendimento.save()

    Repasse.objects.filter(lancamento=lancamento, status=Repasse.Status.A_PAGAR).delete()  # reprocessa se ja havia
    if Repasse.objects.filter(lancamento=lancamento).exists():
        raise ErroFinanceiro("O repasse deste atendimento já foi pago; não é possível alterar o retorno.")
    if recebido > 0:
        lancamento.status, lancamento.pago_em = Lancamento.Status.PAGO, agora
        lancamento.save(update_fields=["status", "pago_em", "atualizado_em"])
        _gerar_repasse(request, lancamento, recebido, agora)
    auditoria.registrar(
        request, "convenio_retorno", "lancamento", lancamento.pk, consultorio_id=lancamento.consultorio_id,
        glosa=str(glosa),
    )


# --------------------------------------------------------------------------- recibo


@transaction.atomic
def emitir_recibo(request, lancamento):
    if lancamento.tipo == Lancamento.Tipo.CONVENIO:
        raise ErroFinanceiro("Convênio não gera recibo ao paciente: quem paga é a operadora.")
    if lancamento.status != Lancamento.Status.PAGO:
        raise ErroFinanceiro("O recibo só pode ser emitido depois do pagamento.")
    existente = Recibo.objects.filter(lancamento=lancamento).first()
    if existente:
        return existente

    consultorio = Consultorio.objects.select_for_update().get(pk=lancamento.consultorio_id)  # serializa a numeracao
    if lancamento.pagador_id:
        nome, cpf = lancamento.pagador.nome, lancamento.pagador.cpf
    elif lancamento.paciente_id:
        nome, cpf = lancamento.paciente.nome, lancamento.paciente.cpf
    else:
        nome, cpf = lancamento.profissional.usuario.nome, ""
    if not cpf:
        raise ErroFinanceiro("Cadastre o CPF do paciente ou do pagador: o recibo precisa dele (Receita Saúde).")

    ultimo = Recibo.objects.filter(consultorio=consultorio).order_by("-numero").values_list("numero", flat=True).first()
    numero = (ultimo or 0) + 1
    fuso = ZoneInfo(consultorio.fuso)
    pago_em = timezone.localtime(lancamento.pago_em, fuso)
    valor = f"R$ {lancamento.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    profissional = lancamento.profissional
    referente = lancamento.descricao or "atendimento profissional"
    texto = (
        f"Recebi de {nome}, CPF {formatar_cpf(cpf)}, a quantia de {valor}, referente a {referente}"
        f"{' realizado em ' + f'{timezone.localtime(lancamento.consulta.inicio, fuso):%d/%m/%Y}' if lancamento.consulta_id else ''}.\n\n"
        f"Forma de pagamento: {lancamento.get_forma_pagamento_display() or 'não informada'}. "
        f"Data do pagamento: {pago_em:%d/%m/%Y}."
    )
    pdf = documentos.gerar_pdf(
        titulo=f"Recibo nº {numero:05d}", texto=texto, consultorio_nome=consultorio.nome,
        assinatura=f"{profissional.usuario.nome} - {profissional.conselho} {profissional.numero}/{profissional.uf}",
        local_data=f"{timezone.localtime(timezone.now(), fuso):%d/%m/%Y}",
    )
    caminho = armazenamento().salvar(cripto.cifrar_bytes(pdf))
    recibo = Recibo.objects.create(
        consultorio=consultorio, lancamento=lancamento, numero=numero, nome_pagador=nome, cpf_pagador=cpf,
        caminho=caminho, hash_sha256=hashlib.sha256(pdf).hexdigest(),
    )
    auditoria.registrar(request, "recibo_emitido", "recibo", recibo.pk, consultorio_id=consultorio.pk, numero=numero)
    return recibo


def baixar_recibo(request, recibo) -> bytes:
    pdf = cripto.decifrar_bytes(armazenamento().ler(recibo.caminho))
    auditoria.registrar(request, "recibo_baixado", "recibo", recibo.pk, consultorio_id=recibo.consultorio_id)
    return pdf


def registrar_nota_externa(request, recibo, numero_nota):
    recibo.numero_nota_externa = numero_nota.strip()[:40]
    recibo.save(update_fields=["numero_nota_externa", "atualizado_em"])
    auditoria.registrar(request, "nota_externa_registrada", "recibo", recibo.pk, consultorio_id=recibo.consultorio_id)


# --------------------------------------------------------------------------- aluguel de sala


def gerar_cobrancas_do_mes(request, consultorio, hoje: date):
    """Cria o lancamento do mes para cada cobranca recorrente ativa, uma unica vez por competencia."""
    competencia = primeiro_dia_do_mes(hoje)
    criados = []
    for cobranca in CobrancaRecorrente.objects.filter(consultorio=consultorio, ativa=True).select_related("profissional"):
        if cobranca.ultima_competencia and cobranca.ultima_competencia >= competencia:
            continue
        with transaction.atomic():
            lancamento = Lancamento.objects.create(
                consultorio=consultorio, profissional=cobranca.profissional, tipo=Lancamento.Tipo.ALUGUEL,
                origem=Lancamento.Origem.RECORRENTE, descricao=f"{cobranca.descricao} - {competencia:%m/%Y}",
                valor=cobranca.valor, vencimento=competencia.replace(day=cobranca.dia_vencimento),
            )
            cobranca.ultima_competencia = competencia
            cobranca.save(update_fields=["ultima_competencia", "atualizado_em"])
            auditoria.registrar(request, "cobranca_recorrente_gerada", "lancamento", lancamento.pk, consultorio_id=consultorio.pk)
        criados.append(lancamento)
    return criados
