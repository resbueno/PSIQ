from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissoes import PERFIS_INTERNOS, perfil_requerido

from . import servico
from .models import LoteImportacao
from .servico import ErroImportacao


class ArquivoForm(forms.Form):
    arquivo = forms.FileField(label="Planilha (.csv ou .xlsx)")


@perfil_requerido(*PERFIS_INTERNOS)
def inicio(request):
    form = ArquivoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            lote = servico.criar_lote(request, form.cleaned_data["arquivo"])
        except ErroImportacao as erro:
            form.add_error("arquivo", str(erro))
        else:
            return redirect("importacao:previa", pk=lote.pk)
    recentes = LoteImportacao.objects.filter(consultorio=request.consultorio)[:8]
    return render(request, "importacao/inicio.html", {"form": form, "recentes": recentes})


@perfil_requerido(*PERFIS_INTERNOS)
def modelo(request):
    resposta = HttpResponse(servico.MODELO_CSV.encode("utf-8"), content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = 'attachment; filename="modelo-pacientes.csv"'
    return resposta


def _lote(request, pk):
    return get_object_or_404(LoteImportacao, pk=pk, consultorio=request.consultorio)


@perfil_requerido(*PERFIS_INTERNOS)
def previa(request, pk):
    lote = _lote(request, pk)
    return render(request, "importacao/previa.html", {"lote": lote})


@perfil_requerido(*PERFIS_INTERNOS)
@require_POST
def confirmar(request, pk):
    lote = _lote(request, pk)
    try:
        n = servico.confirmar(request, lote)
        messages.success(request, f"{n} paciente(s) importado(s).")
        return redirect("pacientes:lista")
    except ErroImportacao as erro:
        messages.error(request, str(erro))
        return redirect("importacao:previa", pk=pk)


@perfil_requerido(*PERFIS_INTERNOS)
@require_POST
def cancelar(request, pk):
    lote = _lote(request, pk)
    try:
        servico.cancelar(request, lote)
        messages.success(request, "Importação cancelada. Nada foi cadastrado e os dados da planilha foram descartados.")
    except ErroImportacao as erro:
        messages.error(request, str(erro))
    return redirect("importacao:inicio")
