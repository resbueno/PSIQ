from django.conf import settings


def _ver_como(request, vinculo, consultorio):
    """Botoes 'ver como' (so consultorios de demonstracao; so para o admin original). Devolve None quando nao se aplica."""
    if consultorio is None or consultorio.slug not in settings.MEUPSIQ_CONSULTORIOS_SEM_2FA:
        return None
    from apps.contas.models import Perfil, Vinculo

    original_id = request.session.get("ver_como_original")
    if not original_id and not (vinculo and vinculo.perfil == Perfil.ADMIN):
        return None
    vinculos = (
        Vinculo.objects.filter(consultorio=consultorio, ativo=True, usuario__is_staff=False)
        .select_related("usuario").order_by("perfil", "usuario__nome")
    )
    return {
        "perfis": [
            {"usuario": v.usuario, "perfil": v.get_perfil_display(), "atual": v.usuario_id == request.user.pk}
            for v in vinculos
        ],
        "ativo": bool(original_id),
    }


def contexto_meupsiq(request):
    vinculo = getattr(request, "vinculo", None)
    consultorio = getattr(request, "consultorio", None)
    return {
        "consultorio_ativo": consultorio,
        "perfil_ativo": vinculo.perfil if vinculo else None,
        "perfil_ativo_nome": vinculo.get_perfil_display() if vinculo else None,
        "ver_como": _ver_como(request, vinculo, consultorio) if request.user.is_authenticated else None,
    }
