from datetime import date

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil
from apps.core.permissoes import PERFIS_INTERNOS, perfil_requerido
from apps.pacientes.servico import profissional_da_requisicao

from . import exportacao, saida, servico
from .models import Exportacao


def _data(texto, padrao):
    try:
        return date.fromisoformat(texto)
    except (TypeError, ValueError):
        return padrao


@perfil_requerido(*PERFIS_INTERNOS)
def indice(request):
    hoje = date.today()
    return render(request, "relatorios/indice.html", {
        "tipos": servico.tipos_do_perfil(request.vinculo.perfil), "de": hoje.replace(day=1), "ate": hoje,
    })


@perfil_requerido(*PERFIS_INTERNOS)
@never_cache
def ver(request, tipo):
    if tipo not in servico.TIPOS or request.vinculo.perfil not in servico.TIPOS[tipo][2]:
        raise Http404
    hoje = date.today()
    ini, fim = _data(request.GET.get("de"), hoje.replace(day=1)), _data(request.GET.get("ate"), hoje)
    if fim < ini:
        ini, fim = fim, ini
    try:
        dias = max(1, min(int(request.GET.get("dias", 60)), 3650))
    except ValueError:
        dias = 60
    request.perfil_relatorio_profissional = profissional_da_requisicao(request)
    rel = servico.TIPOS[tipo][1](request, ini, fim, dias=dias)
    formato = request.GET.get("formato", "html")

    if rel.com_nomes:
        auditoria.registrar(request, "relatorio_com_nomes", "relatorio", tipo, formato=formato)
    if formato in ("csv", "pdf"):
        auditoria.registrar(request, "relatorio_exportado", "relatorio", tipo, formato=formato)
        nome = f"{tipo}-{ini:%Y%m%d}-{fim:%Y%m%d}"
        if formato == "csv":
            resposta = HttpResponse(saida.para_csv(rel), content_type="text/csv; charset=utf-8")
            resposta["Content-Disposition"] = content_disposition_header(True, f"{nome}.csv")
        else:
            pdf = saida.para_pdf(rel, consultorio_nome=request.consultorio.nome, periodo=f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y}")
            resposta = HttpResponse(pdf, content_type="application/pdf")
            resposta["Content-Disposition"] = content_disposition_header(True, f"{nome}.pdf")
        return resposta
    return render(request, "relatorios/ver.html", {"rel": rel, "tipo": tipo, "ini": ini, "fim": fim, "dias": dias})


# --------------------------------------------------------------------------- exportacao completa (sempre liberada)


@perfil_requerido(*PERFIS_INTERNOS)
@never_cache
def pagina_exportacao(request):
    perfil = request.vinculo.perfil
    return render(request, "relatorios/exportacao.html", {
        "pode_consultorio": perfil == Perfil.ADMIN, "pode_prontuarios": profissional_da_requisicao(request) is not None,
        "historico": Exportacao.objects.filter(consultorio=request.consultorio)[:15] if perfil == Perfil.ADMIN else [],
    })


def _zip_resposta(dados, nome):
    resposta = HttpResponse(dados, content_type="application/zip")
    resposta["Content-Disposition"] = content_disposition_header(True, nome)
    resposta["Cache-Control"] = "no-store"
    return resposta


@perfil_requerido(Perfil.ADMIN)
@require_POST
def exportar_consultorio(request):
    return _zip_resposta(exportacao.gerar_zip_consultorio(request), f"psiq-dados-{date.today():%Y%m%d}.zip")


@perfil_requerido(Perfil.PROFISSIONAL)
@require_POST
def exportar_prontuarios(request):
    if not request.user.segundo_fator_ativo:  # leitura em massa de prontuarios exige 2FA
        messages.warning(request, "Ative a verificação em duas etapas para exportar prontuários.")
        return redirect("contas:configurar_2fa")
    return _zip_resposta(exportacao.gerar_zip_prontuarios(request, request.user.profissional), f"psiq-prontuarios-{date.today():%Y%m%d}.zip")
