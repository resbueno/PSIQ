def contexto_psiq(request):
    vinculo = getattr(request, "vinculo", None)
    consultorio = getattr(request, "consultorio", None)
    return {
        "consultorio_ativo": consultorio,
        "perfil_ativo": vinculo.perfil if vinculo else None,
        "perfil_ativo_nome": vinculo.get_perfil_display() if vinculo else None,
    }
