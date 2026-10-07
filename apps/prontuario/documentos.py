"""Modelos de documento (texto com marcadores) e geracao de PDF. Sem avaliacao de codigo: so substituicao de marcadores."""

import re
from zoneinfo import ZoneInfo

from django.utils import timezone
from fpdf import FPDF

from apps.core.validadores import formatar_cpf

MARCADORES = {
    "paciente_nome": "Nome do paciente",
    "paciente_cpf": "CPF do paciente",
    "data_consulta": "Data da consulta",
    "hora_inicio": "Hora de início",
    "hora_fim": "Hora de término",
    "profissional_nome": "Nome do profissional",
    "registro_profissional": "Registro no conselho (ex.: CRP 12345/SP)",
    "consultorio_nome": "Nome do consultório",
    "data_hoje": "Data de hoje",
}

MODELOS_PADRAO = [
    ("comparecimento", "Declaração de comparecimento",
     "Declaro, para os devidos fins, que {{paciente_nome}} compareceu a atendimento em {{data_consulta}}, "
     "das {{hora_inicio}} às {{hora_fim}}."),
    ("declaracao", "Declaração",
     "Declaro, para os devidos fins, que {{paciente_nome}} (CPF {{paciente_cpf}}) encontra-se em acompanhamento "
     "neste consultório."),
    ("atestado", "Atestado",
     "Atesto, para os devidos fins, que {{paciente_nome}} (CPF {{paciente_cpf}}) esteve em atendimento em "
     "{{data_consulta}}."),
    ("relatorio", "Relatório",
     "Relatório referente a {{paciente_nome}}.\n\n[Descreva aqui.]"),
]


def valores(*, paciente, profissional, consultorio, consulta=None):
    fuso = ZoneInfo(consultorio.fuso)
    dados = {
        "paciente_nome": paciente.nome,
        "paciente_cpf": formatar_cpf(paciente.cpf) if paciente.cpf else "não informado",
        "data_consulta": "",
        "hora_inicio": "",
        "hora_fim": "",
        "profissional_nome": profissional.usuario.nome,
        "registro_profissional": f"{profissional.conselho} {profissional.numero}/{profissional.uf}",
        "consultorio_nome": consultorio.nome,
        "data_hoje": f"{timezone.localtime(timezone.now(), fuso):%d/%m/%Y}",
    }
    if consulta is not None:
        inicio = timezone.localtime(consulta.inicio, fuso)
        fim = timezone.localtime(consulta.fim, fuso)
        dados.update(data_consulta=f"{inicio:%d/%m/%Y}", hora_inicio=f"{inicio:%H:%M}", hora_fim=f"{fim:%H:%M}")
    return dados


def preencher(texto_base: str, dados: dict) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: dados.get(m.group(1), m.group(0)), texto_base)


def _latin1(texto: str) -> str:
    # fontes padrao do PDF cobrem Latin-1 (acentos do portugues); o resto vira "?".
    return texto.replace("–", "-").replace("—", "-").replace("“", '"').replace("”", '"').replace("’", "'").encode("latin-1", "replace").decode("latin-1")


def gerar_pdf(*, titulo: str, texto: str, consultorio_nome: str, assinatura: str, local_data: str) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, _latin1(consultorio_nome), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, _latin1(titulo.upper()), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 7, _latin1(texto), align="J")
    pdf.ln(14)
    pdf.cell(0, 7, _latin1(local_data), new_x="LMARGIN", new_y="NEXT", align="R")
    pdf.ln(22)
    pdf.cell(0, 6, "_" * 42, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 6, _latin1(assinatura), new_x="LMARGIN", new_y="NEXT", align="C")
    return bytes(pdf.output())
