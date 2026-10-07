from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil, Vinculo


@login_required
def painel(request):
    if request.vinculo is None:
        return redirect("contas:escolher_consultorio")
    contexto = {}
    if request.vinculo.perfil == Perfil.ADMIN:
        contexto["usuarios_ativos"] = Vinculo.objects.filter(consultorio=request.consultorio, ativo=True).count()
        contexto["eventos"] = Auditoria.objects.all()[:8]
    return render(request, "core/painel.html", contexto)
