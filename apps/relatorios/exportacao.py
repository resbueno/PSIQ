"""Exportacao completa de dados (LGPD, portabilidade, fim de contrato). O arquivo e gerado na hora e nao fica guardado.

- Consultorio (admin): cadastro, agenda, financeiro, auditoria e arquivos sem conteudo clinico. NUNCA inclui prontuarios:
  o admin nao os le, entao cada profissional exporta os seus.
- Prontuarios (profissional dono): registros com todas as versoes, anexos e documentos, decifrados."""

import csv
import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal

from django.utils.text import slugify

from apps.agenda.models import Consulta
from apps.auditoria import servico as auditoria
from apps.auditoria.models import Auditoria
from apps.contas.models import Vinculo
from apps.core import cripto
from apps.financeiro.models import AtendimentoConvenio, Convenio, Lancamento, Recibo, Repasse
from apps.pacientes.models import GrupoAtendimento, Pagador, ParticipanteGrupo, Paciente, ResponsavelLegal
from apps.prontuario import chaves, servico as prontuario_servico
from apps.prontuario.armazenamento import armazenamento
from apps.prontuario.models import Documento, Prontuario

from .models import Exportacao
from .saida import _seguro

LEIA_ME = """Exportação de dados do MeuPSIQ

Este arquivo contém dados pessoais e financeiros do consultório. Guarde-o em local protegido e apague as cópias que
não forem necessárias (LGPD).

Os prontuários NÃO fazem parte desta exportação: eles pertencem a cada profissional, que exporta os seus em
"Exportar meus prontuários". Documentos clínicos também ficam de fora; entram apenas declarações de comparecimento e recibos.

Os arquivos .csv usam ponto e vírgula e codificação UTF-8. O arquivo dados.json traz as mesmas tabelas em formato de máquina.
"""


def _texto(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return str(valor)
    return "" if valor is None else str(valor)


def _csv(cabecalhos, linhas) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    escritor.writerow(cabecalhos)
    for linha in linhas:
        escritor.writerow([_seguro(_texto(c)) for c in linha])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _tabelas(consultorio):
    """(nome, cabecalhos, linhas) das tabelas do consultorio. Sem conteudo clinico."""
    c = consultorio
    t = []
    t.append(("usuarios", ["nome", "email", "perfil", "ativo", "registro_conselho"], [
        [v.usuario.nome, v.usuario.email, v.perfil, v.ativo,
         f"{v.usuario.profissional.conselho} {v.usuario.profissional.numero}/{v.usuario.profissional.uf}" if hasattr(v.usuario, "profissional") else ""]
        for v in Vinculo.objects.filter(consultorio=c).select_related("usuario")]))
    campos_pac = ["id", "nome", "cpf", "nascimento", "email", "telefone", "convenio", "carteirinha", "ativo", "ultimo_atendimento_em", "cadastrado_em", "mesclado_com"]
    t.append(("pacientes", campos_pac, [[p.pk, p.nome, p.cpf, p.nascimento, p.email, p.telefone, p.convenio, p.carteirinha, p.ativo, p.ultimo_atendimento_em, p.criado_em, p.mesclado_com_id] for p in Paciente.objects.filter(consultorio=c)]))
    t.append(("responsaveis", ["paciente_id", "nome", "cpf", "telefone", "email", "recebe_avisos", "pagador"], [[r.paciente_id, r.nome, r.cpf, r.telefone, r.email, r.recebe_avisos, r.pode_pagar] for r in ResponsavelLegal.objects.filter(consultorio=c)]))
    t.append(("pagadores", ["paciente_id", "nome", "cpf"], [[p.paciente_id, p.nome, p.cpf] for p in Pagador.objects.filter(consultorio=c)]))
    t.append(("grupos", ["id", "tipo", "nome", "participantes_ids"], [
        [g.pk, g.tipo, g.nome, " ".join(str(x) for x in ParticipanteGrupo.objects.filter(grupo=g).values_list("paciente_id", flat=True))] for g in GrupoAtendimento.objects.filter(consultorio=c)]))
    t.append(("consultas", ["id", "inicio", "fim", "profissional", "paciente_id", "grupo_id", "tipo", "status", "primeira_consulta", "cancelada_por", "cancelamento_tardio"], [
        [x.pk, x.inicio, x.fim, x.profissional.usuario.nome, x.paciente_id, x.grupo_id, x.tipo, x.status, x.primeira_consulta, x.cancelada_por, x.falta_tardia]
        for x in Consulta.objects.filter(consultorio=c).select_related("profissional__usuario")]))
    t.append(("convenios", ["id", "operadora", "ativo"], [[v.pk, v.operadora, v.ativo] for v in Convenio.objects.filter(consultorio=c)]))
    t.append(("lancamentos", ["id", "consulta_id", "paciente_id", "profissional", "tipo", "origem", "descricao", "valor", "vencimento", "status", "forma_pagamento", "pago_em"], [
        [l.pk, l.consulta_id, l.paciente_id, l.profissional.usuario.nome, l.tipo, l.origem, l.descricao, l.valor, l.vencimento, l.status, l.forma_pagamento, l.pago_em]
        for l in Lancamento.objects.filter(consultorio=c).select_related("profissional__usuario")]))
    t.append(("atendimentos_convenio", ["lancamento_id", "status", "carteirinha", "demonstrativo", "valor_recebido", "valor_glosado", "motivo_glosa"], [
        [a.lancamento_id, a.status, a.carteirinha, a.demonstrativo, a.valor_recebido, a.valor_glosado, a.motivo_glosa] for a in AtendimentoConvenio.objects.filter(consultorio=c)]))
    t.append(("repasses", ["lancamento_id", "profissional", "competencia", "base", "valor", "status", "pago_em"], [
        [r.lancamento_id, r.profissional.usuario.nome, r.competencia, r.base, r.valor, r.status, r.pago_em] for r in Repasse.objects.filter(consultorio=c).select_related("profissional__usuario")]))
    t.append(("recibos", ["numero", "lancamento_id", "nome_pagador", "cpf_pagador", "numero_nota_externa", "emitido_em"], [
        [r.numero, r.lancamento_id, r.nome_pagador, r.cpf_pagador, r.numero_nota_externa, r.criado_em] for r in Recibo.objects.filter(consultorio=c)]))
    t.append(("auditoria", ["quando", "usuario_id", "acao", "objeto", "objeto_id", "ip"], [
        [a.criado_em, a.usuario_id, a.acao, a.objeto, a.objeto_id, a.ip] for a in Auditoria.objects.filter(consultorio_id=c.pk)]))
    return t


def gerar_zip_consultorio(request) -> bytes:
    consultorio = request.consultorio
    saida, arquivos = io.BytesIO(), 0
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        tabelas = _tabelas(consultorio)
        dados = {}
        for nome, cabecalhos, linhas in tabelas:
            z.writestr(f"{nome}.csv", _csv(cabecalhos, linhas))
            dados[nome] = [dict(zip(cabecalhos, [_texto(c) for c in linha])) for linha in linhas]
            arquivos += 1
        z.writestr("dados.json", json.dumps({"consultorio": consultorio.nome, "tabelas": dados}, ensure_ascii=False, indent=2))
        z.writestr("LEIA-ME.txt", LEIA_ME)
        for recibo in Recibo.objects.filter(consultorio=consultorio):
            z.writestr(f"recibos/recibo-{recibo.numero:05d}.pdf", cripto.decifrar_bytes(armazenamento().ler(recibo.caminho)))
            arquivos += 1
        for doc in Documento.objects.filter(consultorio=consultorio, chave__isnull=True):  # so sem conteudo clinico
            z.writestr(f"declaracoes/{slugify(doc.titulo)}-{str(doc.pk)[:8]}.pdf", cripto.decifrar_bytes(armazenamento().ler(doc.caminho)))
            arquivos += 1
    _registrar(request, Exportacao.Tipo.CONSULTORIO, arquivos)
    return saida.getvalue()


def gerar_zip_prontuarios(request, profissional) -> bytes:
    """Todos os prontuarios do profissional, decifrados. A leitura fica na auditoria."""
    saida, arquivos = io.BytesIO(), 0
    prontuarios = Prontuario.objects.filter(consultorio=request.consultorio, profissional=profissional, excluido_em__isnull=True).select_related("paciente", "grupo")
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LEIA-ME.txt", "Seus prontuários exportados pelo MeuPSIQ. Conteúdo sigiloso: guarde com o mesmo cuidado do original.\n")
        for i, p in enumerate(prontuarios, start=1):
            pasta = f"{i:03d}-{slugify(p.paciente.nome if p.paciente_id else p.grupo.nome) or 'prontuario'}"
            registros = []
            for r in p.registros.order_by("criado_em"):
                versoes = []
                for v in r.versoes.order_by("numero"):
                    conteudo, cid = prontuario_servico.ler_versao(request, v, auditar=False)
                    versoes.append({"numero": v.numero, "gravada_em": v.criado_em.isoformat(), "conteudo": conteudo, "cid": cid})
                registros.append({"tipo": r.tipo, "criado_em": r.criado_em.isoformat(), "versoes": versoes})
            z.writestr(f"{pasta}/registros.json", json.dumps({"paciente": p.paciente.nome if p.paciente_id else p.grupo.nome, "registros": registros}, ensure_ascii=False, indent=2))
            arquivos += 1
            for a in p.anexos.all():
                z.writestr(f"{pasta}/anexos/{a.pk.hex[:8]}-{slugify(a.nome.rsplit('.', 1)[0]) or 'arquivo'}.{a.nome.rsplit('.', 1)[-1] if '.' in a.nome else 'bin'}", prontuario_servico.baixar_anexo(request, a))
                arquivos += 1
            for d in p.documentos.all():
                z.writestr(f"{pasta}/documentos/{slugify(d.titulo)}-{d.pk.hex[:8]}.pdf", prontuario_servico.baixar_documento(request, d))
                arquivos += 1
    auditoria.registrar(request, "prontuarios_exportados", "exportacao", "", quantidade=prontuarios.count())
    _registrar(request, Exportacao.Tipo.PRONTUARIOS, arquivos)
    return saida.getvalue()


def _registrar(request, tipo, arquivos):
    exportacao = Exportacao.objects.create(consultorio=request.consultorio, solicitada_por_id=request.user.pk, tipo=tipo, arquivos=arquivos)
    auditoria.registrar(request, "exportacao_gerada", "exportacao", exportacao.pk, tipo=tipo, arquivos=arquivos)
    return exportacao
