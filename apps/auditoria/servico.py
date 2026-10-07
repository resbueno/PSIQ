from .models import Auditoria


def _ip(request):
    return request.META.get("REMOTE_ADDR") or None


def registrar(request, acao, objeto="", objeto_id="", usuario=None, consultorio_id=None, **detalhe):
    """Grava um evento de auditoria. `detalhe` nunca pode conter conteudo clinico."""
    usuario = usuario or getattr(request, "user", None)
    usuario_id = usuario.pk if usuario is not None and usuario.is_authenticated else None
    if consultorio_id is None:
        consultorio = getattr(request, "consultorio", None)
        consultorio_id = consultorio.pk if consultorio else None
    return Auditoria.objects.create(
        consultorio_id=consultorio_id,
        usuario_id=usuario_id,
        acao=acao,
        objeto=objeto,
        objeto_id=str(objeto_id),
        ip=_ip(request),
        dispositivo=request.META.get("HTTP_USER_AGENT", "")[:200],
        detalhe=detalhe,
    )
