import uuid

from django.db import models


class Auditoria(models.Model):
    """Registro append-only (trigger no banco bloqueia UPDATE e DELETE). Sob RLS.

    Guarda ids sem chave estrangeira para sobreviver a qualquer remocao futura.
    Nunca grave conteudo clinico em `detalhe`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consultorio_id = models.UUIDField(null=True, blank=True, db_index=True)
    usuario_id = models.UUIDField(null=True, blank=True)
    acao = models.CharField(max_length=60)
    objeto = models.CharField(max_length=60, blank=True)
    objeto_id = models.CharField(max_length=64, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    dispositivo = models.CharField(max_length=200, blank=True)
    detalhe = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.criado_em:%Y-%m-%d %H:%M:%S} {self.acao}"
