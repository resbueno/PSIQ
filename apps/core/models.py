import uuid

from django.db import models


class ModeloBase(models.Model):
    """UUID gerado no cliente (sem RETURNING, compativel com RLS) e carimbos de data."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
