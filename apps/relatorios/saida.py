"""Saidas dos relatorios: CSV (Excel pt-BR) e PDF."""

import csv
import io

from fpdf import FPDF

from apps.prontuario.documentos import _latin1


def _seguro(valor):
    """Evita injecao de formula ao abrir o CSV no Excel/Sheets."""
    texto = "" if valor is None else str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@") and not texto[1:2].isdigit() else texto


def para_csv(rel) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    escritor.writerow([rel.titulo])
    for rotulo, valor in rel.resumo:
        escritor.writerow([_seguro(rotulo), _seguro(valor)])
    escritor.writerow([])
    escritor.writerow(rel.cabecalhos)
    for linha in rel.linhas:
        escritor.writerow([_seguro(c) for c in linha])
    return ("﻿" + buffer.getvalue()).encode("utf-8")  # BOM: o Excel abre com acentos corretos


def para_pdf(rel, *, consultorio_nome: str, periodo: str) -> bytes:
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _latin1(rel.titulo), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 5, _latin1(f"{consultorio_nome} - {periodo}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    for rotulo, valor in rel.resumo:
        pdf.cell(0, 5, _latin1(f"{rotulo}: {valor}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", size=8)
    with pdf.table(text_align="LEFT", line_height=5) as tabela:
        cabecalho = tabela.row()
        for titulo in rel.cabecalhos:
            cabecalho.cell(_latin1(str(titulo)), style=None)
        for linha in rel.linhas:
            linha_pdf = tabela.row()
            for celula in linha:
                linha_pdf.cell(_latin1("" if celula is None else str(celula)))
    return bytes(pdf.output())
