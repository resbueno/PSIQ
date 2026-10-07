"""Relatorios de agenda, financeiro e pacientes. Nenhum traz conteudo clinico nem CID.
Os que listam nomes de pacientes sao marcados `com_nomes` e geram auditoria ao serem vistos."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from apps.agenda.models import AgendaRegra, Consulta
from apps.contas.models import Perfil
from apps.financeiro.models import AtendimentoConvenio, Lancamento, Repasse
from apps.pacientes.models import Paciente
from apps.pacientes.servico import pacientes_visiveis

ZERO = Decimal("0.00")


@dataclass
class Relatorio:
    titulo: str
    cabecalhos: list
    linhas: list
    resumo: list = field(default_factory=list)
    com_nomes: bool = False


def _intervalo(consultorio, ini: date, fim: date):
    fuso = ZoneInfo(consultorio.fuso)
    return (datetime.combine(ini, datetime.min.time(), tzinfo=fuso), datetime.combine(fim + timedelta(days=1), datetime.min.time(), tzinfo=fuso))


def _pct(parte, total):
    return f"{(parte / total * 100):.1f}%".replace(".", ",") if total else "—"


def _brl(valor):
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------- agenda


def agenda(request, ini, fim, **_):
    consultorio = request.consultorio
    a, b = _intervalo(consultorio, ini, fim)
    consultas = Consulta.objects.filter(consultorio=consultorio, inicio__gte=a, inicio__lt=b).select_related("profissional__usuario")
    if request.perfil_relatorio_profissional:
        consultas = consultas.filter(profissional=request.perfil_relatorio_profissional)
    grupos = defaultdict(list)
    for c in consultas:
        grupos[c.profissional].append(c)

    dias = [ini + timedelta(days=i) for i in range((fim - ini).days + 1)]
    linhas, totais = [], defaultdict(int)
    for profissional, lista in sorted(grupos.items(), key=lambda kv: kv[0].usuario.nome):
        contagem = defaultdict(int)
        minutos_ocupados = 0
        for c in lista:
            contagem[c.status] += 1
            if c.status != Consulta.Status.CANCELADA:
                minutos_ocupados += int((c.fim - c.inicio).total_seconds() // 60)
        regras = list(AgendaRegra.objects.filter(profissional=profissional))
        minutos_disponiveis = sum(
            int((datetime.combine(d, r.hora_fim) - datetime.combine(d, r.hora_inicio)).total_seconds() // 60)
            for d in dias for r in regras if r.dia_semana == d.weekday()
        )
        tardios = sum(1 for c in lista if c.falta_tardia)
        por_paciente = sum(1 for c in lista if c.cancelada_por == Consulta.CanceladoPor.PACIENTE)
        realizadas, faltas = contagem["realizada"], contagem["faltou"]
        linhas.append([
            profissional.usuario.nome, len(lista), realizadas, faltas, _pct(faltas, realizadas + faltas),
            contagem["cancelada"], por_paciente, tardios, _pct(minutos_ocupados, minutos_disponiveis) if minutos_disponiveis else "—",
        ])
        for chave, valor in (("total", len(lista)), ("realizadas", realizadas), ("faltas", faltas), ("cancelamentos", contagem["cancelada"])):
            totais[chave] += valor
    resumo = [("Consultas no período", totais["total"]), ("Realizadas", totais["realizadas"]), ("Faltas", totais["faltas"]),
              ("Taxa de faltas", _pct(totais["faltas"], totais["realizadas"] + totais["faltas"])), ("Cancelamentos", totais["cancelamentos"])]
    return Relatorio(
        "Agenda: faltas, cancelamentos e ocupação",
        ["Profissional", "Consultas", "Realizadas", "Faltas", "% faltas", "Cancelamentos", "Cancelados pelo paciente", "Cancelamentos tardios", "Ocupação"],
        linhas, resumo,
    )


# --------------------------------------------------------------------------- financeiro


def _lancamentos(request):
    qs = Lancamento.objects.filter(consultorio=request.consultorio).select_related("paciente", "profissional__usuario", "convenio")
    return qs.filter(profissional=request.perfil_relatorio_profissional) if request.perfil_relatorio_profissional else qs


def faturamento(request, ini, fim, **_):
    a, b = _intervalo(request.consultorio, ini, fim)
    pagos = _lancamentos(request).filter(status=Lancamento.Status.PAGO, pago_em__gte=a, pago_em__lt=b)
    pendentes = _lancamentos(request).filter(status=Lancamento.Status.PENDENTE, vencimento__gte=ini, vencimento__lte=fim)
    por_prof = defaultdict(lambda: {"recebido": ZERO, "particular": ZERO, "convenio": ZERO, "aluguel": ZERO, "areceber": ZERO})
    for l in pagos:
        nome = l.profissional.usuario.nome
        valor = l.valor
        if l.tipo == Lancamento.Tipo.CONVENIO and hasattr(l, "atendimento_convenio") and l.atendimento_convenio.valor_recebido is not None:
            valor = l.atendimento_convenio.valor_recebido
        por_prof[nome]["recebido"] += valor
        por_prof[nome][l.tipo] += valor
    for l in pendentes:
        por_prof[l.profissional.usuario.nome]["areceber"] += l.valor
    linhas = [[n, _brl(v["recebido"]), _brl(v["particular"]), _brl(v["convenio"]), _brl(v["aluguel"]), _brl(v["areceber"])] for n, v in sorted(por_prof.items())]
    total = sum((v["recebido"] for v in por_prof.values()), ZERO)
    areceber = sum((v["areceber"] for v in por_prof.values()), ZERO)
    return Relatorio(
        "Financeiro: faturamento", ["Profissional", "Recebido (R$)", "Particular", "Convênio", "Aluguel de sala", "A receber no período"],
        linhas, [("Recebido no período", f"R$ {_brl(total)}"), ("A receber (vencimentos do período)", f"R$ {_brl(areceber)}")],
    )


def inadimplencia(request, ini, fim, **_):
    hoje = date.today()
    atrasados = _lancamentos(request).filter(status=Lancamento.Status.PENDENTE, vencimento__lt=hoje).exclude(tipo=Lancamento.Tipo.CONVENIO).order_by("vencimento")
    linhas = [[l.paciente.nome if l.paciente_id else (l.descricao or "—"), l.profissional.usuario.nome, l.vencimento.strftime("%d/%m/%Y"), (hoje - l.vencimento).days, _brl(l.valor)] for l in atrasados]
    total = sum((l.valor for l in atrasados), ZERO)
    return Relatorio(
        "Financeiro: inadimplência", ["Paciente", "Profissional", "Vencimento", "Dias em atraso", "Valor (R$)"], linhas,
        [("Lançamentos em atraso", len(linhas)), ("Total em atraso", f"R$ {_brl(total)}")], com_nomes=True,
    )


def repasses(request, ini, fim, **_):
    qs = Repasse.objects.filter(consultorio=request.consultorio, competencia__gte=ini.replace(day=1), competencia__lte=fim).select_related("profissional__usuario")
    if request.perfil_relatorio_profissional:
        qs = qs.filter(profissional=request.perfil_relatorio_profissional)
    por = defaultdict(lambda: {"base": ZERO, "valor": ZERO, "pago": ZERO, "n": 0})
    for r in qs:
        chave = (r.competencia, r.profissional.usuario.nome)
        por[chave]["base"] += r.base
        por[chave]["valor"] += r.valor
        por[chave]["n"] += 1
        if r.status == Repasse.Status.PAGO:
            por[chave]["pago"] += r.valor
    linhas = [[f"{c:%m/%Y}", n, v["n"], _brl(v["base"]), _brl(v["valor"]), _brl(v["pago"]), _brl(v["valor"] - v["pago"])] for (c, n), v in sorted(por.items())]
    total = sum((v["valor"] for v in por.values()), ZERO)
    return Relatorio(
        "Financeiro: repasses", ["Competência", "Profissional", "Atendimentos", "Base (R$)", "Repasse (R$)", "Já pago (R$)", "A pagar (R$)"], linhas,
        [("Total de repasses", f"R$ {_brl(total)}")],
    )


def glosas(request, ini, fim, **_):
    a, b = _intervalo(request.consultorio, ini, fim)
    qs = AtendimentoConvenio.objects.filter(consultorio=request.consultorio, retorno_em__gte=a, retorno_em__lt=b).select_related("lancamento__convenio")
    if request.perfil_relatorio_profissional:
        qs = qs.filter(lancamento__profissional=request.perfil_relatorio_profissional)
    por = defaultdict(lambda: {"n": 0, "faturado": ZERO, "recebido": ZERO, "glosado": ZERO, "glosas": 0})
    for at in qs:
        operadora = at.lancamento.convenio.operadora if at.lancamento.convenio_id else "—"
        por[operadora]["n"] += 1
        por[operadora]["faturado"] += at.lancamento.valor
        por[operadora]["recebido"] += at.valor_recebido or ZERO
        por[operadora]["glosado"] += at.valor_glosado
        por[operadora]["glosas"] += 1 if at.valor_glosado > 0 else 0
    linhas = [[op, v["n"], _brl(v["faturado"]), _brl(v["recebido"]), _brl(v["glosado"]), _pct(v["glosado"], v["faturado"]), v["glosas"]] for op, v in sorted(por.items())]
    total = sum((v["glosado"] for v in por.values()), ZERO)
    return Relatorio(
        "Convênios: glosas por operadora", ["Operadora", "Atendimentos com retorno", "Faturado (R$)", "Recebido (R$)", "Glosado (R$)", "% glosa", "Atendimentos glosados"], linhas,
        [("Total glosado", f"R$ {_brl(total)}")],
    )


# --------------------------------------------------------------------------- pacientes


def pacientes_novos(request, ini, fim, **_):
    a, b = _intervalo(request.consultorio, ini, fim)
    qs = pacientes_visiveis(request).filter(criado_em__gte=a, criado_em__lt=b).order_by("criado_em")
    linhas = [[p.nome, timezone.localtime(p.criado_em, ZoneInfo(request.consultorio.fuso)).strftime("%d/%m/%Y"), p.convenio or "Particular"] for p in qs]
    return Relatorio("Pacientes: novos no período", ["Paciente", "Cadastro", "Convênio"], linhas, [("Novos pacientes", len(linhas))], com_nomes=True)


def pacientes_sem_consulta(request, ini, fim, dias=60, **_):
    limite = timezone.now() - timedelta(days=dias)
    qs = pacientes_visiveis(request).filter(ativo=True).filter(Q(ultimo_atendimento_em__lt=limite) | Q(ultimo_atendimento_em__isnull=True)).order_by("ultimo_atendimento_em", "nome")
    linhas = [[p.nome, p.ultimo_atendimento_em.strftime("%d/%m/%Y") if p.ultimo_atendimento_em else "Nunca", p.telefone or "—"] for p in qs]
    return Relatorio(f"Pacientes: sem consulta há mais de {dias} dias", ["Paciente", "Último atendimento", "Telefone"], linhas, [("Pacientes", len(linhas))], com_nomes=True)


def pacientes_resumo(request, ini, fim, **_):
    base = pacientes_visiveis(request)
    a, b = _intervalo(request.consultorio, ini, fim)
    ativos = base.filter(ativo=True).count()
    inativos = base.filter(ativo=False).count()
    novos = base.filter(criado_em__gte=a, criado_em__lt=b).count()
    convenio = base.filter(ativo=True).exclude(convenio="").count()
    return Relatorio(
        "Pacientes: visão geral", ["Indicador", "Valor"],
        [["Pacientes ativos", ativos], ["Pacientes inativos", inativos], ["Novos no período", novos], ["Ativos com convênio", convenio], ["Ativos particulares", ativos - convenio]],
        [("Pacientes ativos", ativos)],
    )


# --------------------------------------------------------------------------- catalogo por perfil

TODOS = (Perfil.PROFISSIONAL, Perfil.ASSISTENTE, Perfil.ADMIN)
OPERACIONAL = (Perfil.ASSISTENTE, Perfil.ADMIN)

TIPOS = {
    "agenda": ("Agenda: faltas, cancelamentos e ocupação", agenda, TODOS),
    "faturamento": ("Financeiro: faturamento", faturamento, TODOS),
    "inadimplencia": ("Financeiro: inadimplência", inadimplencia, TODOS),
    "repasses": ("Financeiro: repasses", repasses, (Perfil.PROFISSIONAL, Perfil.ADMIN)),
    "glosas": ("Convênios: glosas por operadora", glosas, TODOS),
    "pacientes-resumo": ("Pacientes: visão geral", pacientes_resumo, TODOS),
    "pacientes-novos": ("Pacientes: novos no período", pacientes_novos, TODOS),
    "pacientes-sem-consulta": ("Pacientes: sem consulta há X dias", pacientes_sem_consulta, TODOS),
}


def tipos_do_perfil(perfil):
    return {chave: titulo for chave, (titulo, _, perfis) in TIPOS.items() if perfil in perfis}
