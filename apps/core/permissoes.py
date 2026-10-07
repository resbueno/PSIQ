from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


from apps.contas.models import Perfil

PERFIS_INTERNOS = (Perfil.PROFISSIONAL, Perfil.ASSISTENTE, Perfil.ADMIN)


def perfil_requerido(*perfis):
    """Camada 1 da autorizacao: o perfil do vinculo ativo precisa estar na lista."""

    def decorador(view):
        @wraps(view)
        @login_required
        def envolvida(request, *args, **kwargs):
            if request.vinculo is None:
                return redirect("contas:escolher_consultorio")
            if request.vinculo.perfil not in perfis:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return envolvida

    return decorador


def consultorio_requerido(view):
    @wraps(view)
    @login_required
    def envolvida(request, *args, **kwargs):
        if request.vinculo is None:
            return redirect("contas:escolher_consultorio")
        return view(request, *args, **kwargs)

    return envolvida
