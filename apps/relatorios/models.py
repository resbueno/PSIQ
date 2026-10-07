from django.db import models

from apps.core.models import ModeloBase


class Exportacao(ModeloBase):
    """Registro de cada exportacao de dados (o arquivo e gerado na hora e nao fica guardado)."""

    class Tipo(models.TextChoices):
        CONSULTORIO = "consultorio", "Dados do consultório"
        PRONTUARIOS = "prontuarios", "Prontuários do profissional"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    solicitada_por_id = models.UUIDField()
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    arquivos = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-criado_em"]
