from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil

from .models import Paciente, ProfissionalPaciente


def profissional_da_requisicao(request):
    """Registro de profissional do usuario logado, se o vinculo ativo for de profissional."""
    if request.vinculo and request.vinculo.perfil == Perfil.PROFISSIONAL:
        return getattr(request.user, "profissional", None)
    return None


def pacientes_visiveis(request, incluir_mesclados=False):
    """Camada 2 de autorizacao: profissional ve so os seus pacientes; assistente e admin, todos do consultorio."""
    consulta = Paciente.objects.filter(consultorio=request.consultorio)
    if not incluir_mesclados:
        consulta = consulta.filter(mesclado_em__isnull=True)
    profissional = profissional_da_requisicao(request)
    if profissional is not None:
        consulta = consulta.filter(profissionais__profissional=profissional)
    return consulta


def vincular_profissional(paciente, profissional):
    ProfissionalPaciente.objects.get_or_create(
        consultorio_id=paciente.consultorio_id, paciente=paciente, profissional=profissional
    )


@transaction.atomic
def mesclar(request, destino, origem):
    """Move todo o historico de `origem` para `destino` e marca `origem` como mesclado (nada e apagado)."""
    if destino.pk == origem.pk or destino.consultorio_id != origem.consultorio_id:
        raise ValueError("Mesclagem inválida.")
    if destino.mesclado_em or origem.mesclado_em:
        raise ValueError("Cadastro já mesclado.")

    origem.mesclado_em = timezone.now()
    origem.mesclado_com = destino
    origem.ativo = False
    origem.save(update_fields=["mesclado_em", "mesclado_com", "ativo", "atualizado_em"])

    # Reaponta toda relacao que aponta para Paciente (inclusive de apps futuros).
    for rel in Paciente._meta.related_objects:
        modelo, campo = rel.related_model, rel.field.name
        if modelo is Paciente:
            continue
        for obj in modelo.objects.filter(**{campo: origem}):
            try:
                with transaction.atomic():
                    setattr(obj, campo, destino)
                    obj.save(update_fields=[campo])
            except IntegrityError:
                obj.delete()  # vinculo duplicado (ex.: mesmo profissional ja ligado ao destino)

    for campo in ("cpf", "nascimento", "email", "telefone", "convenio", "carteirinha"):
        if not getattr(destino, campo) and getattr(origem, campo):
            setattr(destino, campo, getattr(origem, campo))
    if origem.ultimo_atendimento_em and (
        not destino.ultimo_atendimento_em or origem.ultimo_atendimento_em > destino.ultimo_atendimento_em
    ):
        destino.ultimo_atendimento_em = origem.ultimo_atendimento_em
    destino.save()
    auditoria.registrar(request, "paciente_mesclado", "paciente", destino.pk, origem_id=str(origem.pk))
    return destino
