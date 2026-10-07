from django.db import models
from django.utils import timezone

from apps.core.models import ModeloBase


class AcessoSuporte(ModeloBase):
    """Autorizacao temporaria do cliente para o operador da plataforma entrar na conta (somente leitura).
    O prontuario continua fora do alcance por regra do sistema. Tudo fica na auditoria do consultorio."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    operador = models.ForeignKey("contas.Usuario", on_delete=models.CASCADE, related_name="+")
    autorizado_por_id = models.UUIDField()
    inicio = models.DateTimeField(default=timezone.now)
    fim = models.DateTimeField()
    motivo = models.CharField(max_length=300)
    revogado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-inicio"]

    @property
    def vigente(self) -> bool:
        agora = timezone.now()
        return self.revogado_em is None and self.inicio <= agora < self.fim
