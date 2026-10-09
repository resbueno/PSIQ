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


def segundo_fator_satisfeito(request):
    """2FA ativo, ou consultorio de demonstracao listado em MEUPSIQ_CONSULTORIOS_SEM_2FA (nunca em producao)."""
    from django.conf import settings

    if request.user.segundo_fator_ativo:
        return True
    consultorio = getattr(request, "consultorio", None)
    return bool(consultorio and consultorio.slug in settings.MEUPSIQ_CONSULTORIOS_SEM_2FA)


def consultorio_de_demonstracao(request):
    """Consultorio listado em MEUPSIQ_CONSULTORIOS_SEM_2FA (so demonstracao/homologacao): o admin le prontuarios."""
    from django.conf import settings

    consultorio = getattr(request, "consultorio", None)
    return bool(consultorio and consultorio.slug in settings.MEUPSIQ_CONSULTORIOS_SEM_2FA)
