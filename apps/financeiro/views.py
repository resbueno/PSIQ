from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from apps.agenda.models import Consulta
from apps.agenda.servico import profissionais_do_consultorio
from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil
from apps.core.permissoes import PERFIS_INTERNOS, perfil_requerido
from apps.pacientes.servico import pacientes_visiveis, profissional_da_requisicao

from . import servico
from .forms import (
    CobrancaRecorrenteForm, ConvenioForm, FaturarForm, LancamentoManualForm, NotaExternaForm, PagamentoForm,
    RegraRepasseForm, RetornoForm, TabelaValorForm, ValorConsultaForm,
)
from .models import AtendimentoConvenio, CobrancaRecorrente, Convenio, Lancamento, Recibo, RegraRepasse, Repasse, TabelaValor
from .servico import ErroFinanceiro

OPERACIONAL = (Perfil.ASSISTENTE, Perfil.ADMIN)


def _lancamentos(request):
    qs = Lancamento.objects.filter(consultorio=request.consultorio).select_related(
        "paciente", "profissional__usuario", "convenio", "consulta"
    )
    proprio = profissional_da_requisicao(request)
    return qs.filter(profissional=proprio) if proprio else qs


def _repasses(request):
    qs = Repasse.objects.filter(consultorio=request.consultorio).select_related("profissional__usuario", "lancamento__paciente")
    proprio = profissional_da_requisicao(request)
    return qs.filter(profissional=proprio) if proprio else qs


# --------------------------------------------------------------------------- lancamentos


@perfil_requerido(*PERFIS_INTERNOS)
def lancamentos(request):
    qs = _lancamentos(request)
    status, mes = request.GET.get("status", ""), request.GET.get("mes", "")
    if status == "atrasado":
        qs = qs.filter(status=Lancamento.Status.PENDENTE, vencimento__lt=date.today()).exclude(tipo=Lancamento.Tipo.CONVENIO)
    elif status in Lancamento.Status.values:
        qs = qs.filter(status=status)
    if mes:
        try:
            ano, m = (int(x) for x in mes.split("-"))
            qs = qs.filter(vencimento__year=ano, vencimento__month=m)
        except ValueError:
            pass
    total = lambda **f: qs.filter(**f).aggregate(t=Sum("valor"))["t"] or Decimal("0.00")
    return render(request, "financeiro/lancamentos.html", {
        "lancamentos": qs[:300], "status": status, "mes": mes,
        "pendente": total(status=Lancamento.Status.PENDENTE), "pago": total(status=Lancamento.Status.PAGO),
        "pode_operar": request.vinculo.perfil in OPERACIONAL,
    })


@perfil_requerido(*PERFIS_INTERNOS)
def lancamento(request, pk):
    obj = get_object_or_404(_lancamentos(request), pk=pk)
    pode_operar = request.vinculo.perfil in OPERACIONAL
    if request.method == "POST":
        if not pode_operar:
            raise Http404
        acao = request.POST.get("acao")
        try:
            if acao == "pagar":
                form = PagamentoForm(request.POST)
                if not form.is_valid():
                    raise ErroFinanceiro("Escolha a forma de pagamento.")
                servico.registrar_pagamento(request, obj, forma=form.cleaned_data["forma_pagamento"])
            elif acao == "desfazer":
                servico.desfazer_pagamento(request, obj)
            elif acao == "cancelar":
                servico.cancelar_lancamento(request, obj)
            elif acao == "recibo":
                servico.emitir_recibo(request, obj)
            elif acao == "nota":
                form = NotaExternaForm(request.POST)
                if not form.is_valid() or not hasattr(obj, "recibo"):
                    raise ErroFinanceiro("Informe o número da nota (o recibo precisa existir).")
                servico.registrar_nota_externa(request, obj.recibo, form.cleaned_data["numero_nota"])
            else:
                raise ErroFinanceiro("Ação desconhecida.")
        except ErroFinanceiro as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Pronto.")
        return redirect("financeiro:lancamento", pk=pk)
    return render(request, "financeiro/lancamento.html", {
        "l": obj, "pode_operar": pode_operar, "pagamento_form": PagamentoForm(), "nota_form": NotaExternaForm(),
        "recibo": Recibo.objects.filter(lancamento=obj).first(), "repasse": Repasse.objects.filter(lancamento=obj).first(),
    })


@perfil_requerido(*OPERACIONAL)
def lancamento_manual(request):
    profissionais = profissionais_do_consultorio(request.consultorio)
    form = LancamentoManualForm(request.POST or None, profissionais=profissionais, pacientes=pacientes_visiveis(request).filter(ativo=True))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        novo = Lancamento.objects.create(
            consultorio=request.consultorio, paciente=d["paciente"], pagador=getattr(d["paciente"], "pagador", None),
            profissional=d["profissional"], tipo=Lancamento.Tipo.PARTICULAR, origem=Lancamento.Origem.MANUAL,
            descricao=d["descricao"], valor=servico.arredondar(d["valor"]), vencimento=d["vencimento"],
        )
        auditoria.registrar(request, "lancamento_criado", "lancamento", novo.pk, tipo="manual")
        return redirect("financeiro:lancamento", pk=novo.pk)
    return render(request, "financeiro/form.html", {"form": form, "titulo": "Novo lançamento"})


@perfil_requerido(*OPERACIONAL)
def lancar_consulta(request, consulta_pk):
    """Cobranca de uma consulta realizada quando nao havia valor configurado."""
    consulta = get_object_or_404(Consulta.objects.filter(consultorio=request.consultorio).select_related("paciente", "profissional__usuario", "consultorio"), pk=consulta_pk)
    if consulta.status != Consulta.Status.REALIZADA:
        messages.error(request, "Só consultas realizadas podem ser cobradas.")
        return redirect("agenda:consulta", pk=consulta_pk)
    existente = Lancamento.objects.filter(consulta=consulta, origem=Lancamento.Origem.CONSULTA).first()
    if existente:
        return redirect("financeiro:lancamento", pk=existente.pk)
    form = ValorConsultaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            novo = servico.gerar_lancamento(request, consulta, valor=form.cleaned_data["valor"])
        except ErroFinanceiro as erro:
            form.add_error(None, str(erro))
        else:
            return redirect("financeiro:lancamento", pk=novo.pk)
    return render(request, "financeiro/form.html", {"form": form, "titulo": "Lançar cobrança da consulta"})


@perfil_requerido(*PERFIS_INTERNOS)
def recibo_baixar(request, recibo_pk):
    recibo = get_object_or_404(Recibo.objects.filter(consultorio=request.consultorio), pk=recibo_pk)
    get_object_or_404(_lancamentos(request), pk=recibo.lancamento_id)  # profissional so baixa os seus
    pdf = servico.baixar_recibo(request, recibo)
    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = content_disposition_header(True, f"recibo-{recibo.numero:05d}.pdf")
    return resposta


# --------------------------------------------------------------------------- convenio


@perfil_requerido(*OPERACIONAL)
def convenio_fila(request):
    base = AtendimentoConvenio.objects.filter(consultorio=request.consultorio).select_related(
        "lancamento__paciente", "lancamento__convenio", "lancamento__profissional__usuario"
    )
    return render(request, "financeiro/convenio.html", {
        "realizados": base.filter(status=AtendimentoConvenio.Status.REALIZADO),
        "faturados": base.filter(status=AtendimentoConvenio.Status.FATURADO),
        "glosados": base.filter(status=AtendimentoConvenio.Status.GLOSADO),
        "faturar_form": FaturarForm(), "retorno_form": RetornoForm(),
    })


@perfil_requerido(*OPERACIONAL)
@require_POST
def convenio_faturar(request):
    ids = request.POST.getlist("atendimento")
    atendimentos = list(AtendimentoConvenio.objects.filter(consultorio=request.consultorio, pk__in=ids))
    form = FaturarForm(request.POST)
    if not atendimentos or not form.is_valid():
        messages.error(request, "Marque ao menos um atendimento.")
    else:
        try:
            servico.faturar(request, atendimentos, form.cleaned_data["demonstrativo"])
            messages.success(request, f"{len(atendimentos)} atendimento(s) faturado(s).")
        except ErroFinanceiro as erro:
            messages.error(request, str(erro))
    return redirect("financeiro:convenio")


@perfil_requerido(*OPERACIONAL)
@require_POST
def convenio_retorno(request, pk):
    atendimento = get_object_or_404(AtendimentoConvenio.objects.select_related("lancamento"), pk=pk, consultorio=request.consultorio)
    form = RetornoForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe o valor recebido.")
    else:
        d = form.cleaned_data
        try:
            servico.registrar_retorno(request, atendimento, valor_recebido=d["valor_recebido"], motivo=d["motivo"], demonstrativo=d["demonstrativo"])
            messages.success(request, "Retorno registrado.")
        except ErroFinanceiro as erro:
            messages.error(request, str(erro))
    return redirect("financeiro:convenio")


# --------------------------------------------------------------------------- repasses


@perfil_requerido(*PERFIS_INTERNOS)
def repasses(request):
    qs = _repasses(request)
    mes = request.GET.get("mes", "")
    if mes:
        try:
            ano, m = (int(x) for x in mes.split("-"))
            qs = qs.filter(competencia__year=ano, competencia__month=m)
        except ValueError:
            pass
    return render(request, "financeiro/repasses.html", {
        "repasses": qs[:300], "mes": mes, "a_pagar": qs.filter(status=Repasse.Status.A_PAGAR).aggregate(t=Sum("valor"))["t"] or Decimal("0.00"),
        "pode_pagar": request.vinculo.perfil == Perfil.ADMIN,
    })


@perfil_requerido(Perfil.ADMIN)
@require_POST
def repasse_pagar(request, pk):
    repasse = get_object_or_404(Repasse, pk=pk, consultorio=request.consultorio)
    try:
        servico.marcar_repasse_pago(request, repasse)
        messages.success(request, "Repasse marcado como pago.")
    except ErroFinanceiro as erro:
        messages.error(request, str(erro))
    return redirect("financeiro:repasses")


# --------------------------------------------------------------------------- configuracao (admin)

CONFIG = {
    "convenios": (Convenio, ConvenioForm, "Convênios", ("operadora", "ativo")),
    "valores": (TabelaValor, TabelaValorForm, "Valores das sessões", ("profissional", "convenio", "tipo", "valor")),
    "regras-repasse": (RegraRepasse, RegraRepasseForm, "Regras de repasse", ("profissional", "aplicacao", "percentual", "valor_fixo")),
    "recorrentes": (CobrancaRecorrente, CobrancaRecorrenteForm, "Aluguel de sala e cobranças recorrentes", ("profissional", "descricao", "valor", "dia_vencimento", "ativa")),
}


def _config(chave):
    if chave not in CONFIG:
        raise Http404
    return CONFIG[chave]


def _texto(obj, campo):
    valor = getattr(obj, f"get_{campo}_display", None)
    valor = valor() if valor else getattr(obj, campo)
    if valor is None or valor == "":
        return "—"
    return "Sim" if valor is True else "Não" if valor is False else str(valor)


@perfil_requerido(Perfil.ADMIN)
def config_lista(request, chave):
    modelo, _, titulo, colunas = _config(chave)
    objetos = modelo.objects.filter(consultorio=request.consultorio)
    linhas = [(o, [_texto(o, c) for c in colunas]) for o in objetos]
    cabecalhos = [modelo._meta.get_field(c).verbose_name.capitalize() for c in colunas]
    return render(request, "financeiro/config_lista.html", {"chave": chave, "titulo": titulo, "cabecalhos": cabecalhos, "linhas": linhas})


@perfil_requerido(Perfil.ADMIN)
def config_form(request, chave, pk=None):
    modelo, form_cls, titulo, _ = _config(chave)
    instancia = get_object_or_404(modelo, pk=pk, consultorio=request.consultorio) if pk else None
    form = form_cls(
        request.POST or None, instance=instancia, consultorio=request.consultorio,
        profissionais=profissionais_do_consultorio(request.consultorio),
    ) if form_cls is not ConvenioForm else form_cls(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.consultorio = request.consultorio
        try:
            with transaction.atomic():
                obj.save()
        except IntegrityError:
            form.add_error(None, "Já existe um cadastro igual a este.")
        else:
            auditoria.registrar(request, "configuracao_financeira_salva", chave, obj.pk)
            return redirect("financeiro:config_lista", chave=chave)
    return render(request, "financeiro/form.html", {"form": form, "titulo": titulo, "voltar": chave})


@perfil_requerido(Perfil.ADMIN)
@require_POST
def config_remover(request, chave, pk):
    modelo, *_ = _config(chave)
    obj = get_object_or_404(modelo, pk=pk, consultorio=request.consultorio)
    try:
        with transaction.atomic():
            obj.delete()
    except Exception:  # protegido por lancamentos existentes (PROTECT)
        messages.error(request, "Este item já foi usado em lançamentos. Desative em vez de remover.")
    else:
        auditoria.registrar(request, "configuracao_financeira_removida", chave, pk)
    return redirect("financeiro:config_lista", chave=chave)
