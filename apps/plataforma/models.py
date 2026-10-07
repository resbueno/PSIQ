from django.db import models

from apps.core.models import ModeloBase


class Plano(ModeloBase):
    class TipoCobranca(models.TextChoices):
        CONSULTORIO = "consultorio", "Por consultório"
        PROFISSIONAL = "profissional", "Por profissional"

    nome = models.CharField(max_length=80, unique=True)
    limite_pacientes_ativos = models.PositiveIntegerField()
    limite_profissionais = models.PositiveIntegerField()
    preco = models.DecimalField(max_digits=10, decimal_places=2)
    tipo_cobranca = models.CharField(max_length=20, choices=TipoCobranca.choices, default=TipoCobranca.CONSULTORIO)

    def __str__(self):
        return self.nome


class Consultorio(ModeloBase):
    class Status(models.TextChoices):
        ATIVO = "ativo", "Ativo"
        CARENCIA = "carencia", "Em carência"
        SOMENTE_LEITURA = "somente_leitura", "Somente leitura"
        ENCERRADO = "encerrado", "Encerrado"

    nome = models.CharField(max_length=160)
    documento = models.CharField("CNPJ ou CPF", max_length=18, blank=True)
    plano = models.ForeignKey(Plano, null=True, blank=True, on_delete=models.PROTECT, related_name="consultorios")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ATIVO)
    fuso = models.CharField(max_length=64, default="America/Sao_Paulo")
    politica_cancelamento_horas = models.PositiveIntegerField(default=24)
    encerrado_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.nome

    @property
    def somente_leitura(self):
        return self.status == self.Status.SOMENTE_LEITURA


class Contrato(ModeloBase):
    class Tipo(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        CONSULTORIO = "consultorio", "Consultório"

    class Status(models.TextChoices):
        ATIVO = "ativo", "Ativo"
        ENCERRADO = "encerrado", "Encerrado"

    consultorio = models.ForeignKey(Consultorio, on_delete=models.PROTECT, related_name="contratos")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    profissional = models.ForeignKey(
        "contas.Profissional", null=True, blank=True, on_delete=models.PROTECT, related_name="contratos"
    )
    vigencia_inicio = models.DateField()
    vigencia_fim = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ATIVO)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tipo="consultorio") | models.Q(profissional__isnull=False),
                name="contrato_individual_exige_profissional",
            )
        ]

    def __str__(self):
        return f"{self.consultorio} ({self.get_tipo_display()})"


class PagamentoPlataforma(ModeloBase):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        PAGO = "pago", "Pago"

    consultorio = models.ForeignKey(Consultorio, on_delete=models.PROTECT, related_name="pagamentos")
    competencia = models.DateField(help_text="Primeiro dia do mês de referência.")
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    pago_em = models.DateTimeField(null=True, blank=True, help_text="Marcado manualmente pelo operador.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["consultorio", "competencia"], name="pagamento_unico_por_competencia")
        ]

    def __str__(self):
        return f"{self.consultorio} {self.competencia:%m/%Y}"
