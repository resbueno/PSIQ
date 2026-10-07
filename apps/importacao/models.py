from django.db import models

from apps.core.models import ModeloBase


class LoteImportacao(ModeloBase):
    """Planilha de pacientes: primeiro uma previa validada, depois a confirmacao. Os dados ficam sob RLS."""

    class Status(models.TextChoices):
        PREVIA = "previa", "Prévia"
        CONCLUIDA = "concluida", "Concluída"
        CANCELADA = "cancelada", "Cancelada"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    criado_por_id = models.UUIDField()
    nome_arquivo = models.CharField(max_length=200)
    linhas = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIA)
    total = models.PositiveIntegerField(default=0)
    validas = models.PositiveIntegerField(default=0)
    duplicadas = models.PositiveIntegerField(default=0)
    com_erro = models.PositiveIntegerField(default=0)
    importadas = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-criado_em"]
