from django.db import models

from apps.core.models import ModeloBase


class CodigoAcesso(ModeloBase):
    """Codigo de uso unico enviado por e-mail (o paciente nao tem senha). Guarda so o hash."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    email = models.EmailField()
    codigo_hash = models.CharField(max_length=64)
    expira_em = models.DateTimeField()
    tentativas = models.PositiveSmallIntegerField(default=0)
    usado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["email", "criado_em"], name="codigo_email_criado_idx")]


class SolicitacaoLGPD(ModeloBase):
    """Canal do titular: pedido de exclusao, copia ou correcao de dados. A equipe responde fora do sistema."""

    class Tipo(models.TextChoices):
        EXCLUSAO = "exclusao", "Exclusão dos meus dados"
        COPIA = "copia", "Cópia dos meus dados"
        CORRECAO = "correcao", "Correção de dados"

    class Status(models.TextChoices):
        ABERTA = "aberta", "Aberta"
        ATENDIDA = "atendida", "Atendida"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.ForeignKey("pacientes.Paciente", on_delete=models.CASCADE, related_name="solicitacoes_lgpd")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    mensagem = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ABERTA)
    atendida_por_id = models.UUIDField(null=True, blank=True)
    atendida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]
