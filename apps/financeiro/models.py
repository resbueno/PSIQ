"""Financeiro. O MeuPSIQ registra e calcula; nao movimenta dinheiro, nao emite NFS-e e nao cobra (docs/01, RF-37)."""

from datetime import date

from django.db import models
from django.db.models import Q

from apps.core.models import ModeloBase


class TipoValor(models.TextChoices):
    CONSULTA = "consulta", "Consulta"
    PRIMEIRA = "primeira_consulta", "Primeira consulta"


class Convenio(ModeloBase):
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    operadora = models.CharField(max_length=120)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["operadora"]
        constraints = [models.UniqueConstraint(fields=["consultorio", "operadora"], name="convenio_unico")]

    def __str__(self):
        return self.operadora


class TabelaValor(ModeloBase):
    """Valor da sessao por profissional. Sem convenio = particular; com convenio = tabela da operadora."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.CASCADE, related_name="+")
    convenio = models.ForeignKey(Convenio, null=True, blank=True, on_delete=models.CASCADE, related_name="valores")
    tipo = models.CharField(max_length=20, choices=TipoValor.choices, default=TipoValor.CONSULTA)
    valor = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profissional", "convenio", "tipo"], condition=Q(convenio__isnull=False), name="valor_convenio_unico"),
            models.UniqueConstraint(fields=["profissional", "tipo"], condition=Q(convenio__isnull=True), name="valor_particular_unico"),
            models.CheckConstraint(condition=Q(valor__gte=0), name="valor_nao_negativo"),
        ]


class Lancamento(ModeloBase):
    class Tipo(models.TextChoices):
        PARTICULAR = "particular", "Particular"
        CONVENIO = "convenio", "Convênio"
        ALUGUEL = "aluguel", "Aluguel de sala"

    class Origem(models.TextChoices):
        CONSULTA = "consulta", "Consulta realizada"
        FALTA_TARDIA = "falta_tardia", "Cancelamento tardio"
        RECORRENTE = "recorrente", "Cobrança recorrente"
        MANUAL = "manual", "Lançamento manual"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        PAGO = "pago", "Pago"
        CANCELADO = "cancelado", "Cancelado"

    class Forma(models.TextChoices):
        PIX = "pix", "Pix"
        DINHEIRO = "dinheiro", "Dinheiro"
        CARTAO = "cartao", "Cartão"
        TRANSFERENCIA = "transferencia", "Transferência"
        OUTRO = "outro", "Outro"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    consulta = models.ForeignKey("agenda.Consulta", null=True, blank=True, on_delete=models.PROTECT, related_name="lancamentos")
    paciente = models.ForeignKey("pacientes.Paciente", null=True, blank=True, on_delete=models.PROTECT, related_name="lancamentos")
    pagador = models.ForeignKey("pacientes.Pagador", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="lancamentos")
    convenio = models.ForeignKey(Convenio, null=True, blank=True, on_delete=models.PROTECT, related_name="lancamentos")
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    origem = models.CharField(max_length=14, choices=Origem.choices, default=Origem.CONSULTA)
    primeira_consulta = models.BooleanField(default=False)
    descricao = models.CharField(max_length=160, blank=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    vencimento = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    forma_pagamento = models.CharField(max_length=14, choices=Forma.choices, blank=True)
    pago_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-vencimento", "-criado_em"]
        constraints = [
            models.UniqueConstraint(fields=["consulta", "origem"], condition=Q(consulta__isnull=False), name="um_lancamento_por_consulta_e_origem"),
            models.CheckConstraint(condition=Q(valor__gte=0), name="lancamento_valor_nao_negativo"),
        ]

    @property
    def atrasado(self) -> bool:
        return self.status == self.Status.PENDENTE and self.tipo != self.Tipo.CONVENIO and self.vencimento < date.today()


class AtendimentoConvenio(ModeloBase):
    class Status(models.TextChoices):
        REALIZADO = "realizado", "Realizado"
        FATURADO = "faturado", "Faturado"
        PAGO = "pago", "Pago"
        GLOSADO = "glosado", "Glosado"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    lancamento = models.OneToOneField(Lancamento, on_delete=models.CASCADE, related_name="atendimento_convenio")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REALIZADO)
    carteirinha = models.CharField(max_length=40, blank=True)
    demonstrativo = models.CharField(max_length=60, blank=True, help_text="Lote ou demonstrativo da operadora.")
    valor_recebido = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valor_glosado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    motivo_glosa = models.CharField(max_length=200, blank=True)
    faturado_em = models.DateTimeField(null=True, blank=True)
    retorno_em = models.DateTimeField(null=True, blank=True)


class Recibo(ModeloBase):
    """Recibo em PDF, em nome do pagador, com os campos que o Receita Saude pede. A nota fiscal e emitida fora."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    lancamento = models.OneToOneField(Lancamento, on_delete=models.PROTECT, related_name="recibo")
    numero = models.PositiveIntegerField()
    nome_pagador = models.CharField(max_length=160)
    cpf_pagador = models.CharField(max_length=11)
    caminho = models.CharField(max_length=300)
    hash_sha256 = models.CharField(max_length=64)
    numero_nota_externa = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["consultorio", "numero"], name="recibo_numero_unico")]


class RegraRepasse(ModeloBase):
    class Aplicacao(models.TextChoices):
        PADRAO = "", "Qualquer atendimento"
        PARTICULAR = "particular", "Particular"
        CONVENIO = "convenio", "Convênio"
        PRIMEIRA = "primeira_consulta", "Primeira consulta"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.CASCADE, related_name="+")
    aplicacao = models.CharField("vale para", max_length=20, choices=Aplicacao.choices, blank=True, default="")
    percentual = models.DecimalField("percentual do profissional (%)", max_digits=5, decimal_places=2, null=True, blank=True)
    valor_fixo = models.DecimalField("valor fixo por atendimento", max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profissional", "aplicacao"], name="regra_repasse_unica"),
            models.CheckConstraint(
                condition=(Q(percentual__isnull=False, valor_fixo__isnull=True) | Q(percentual__isnull=True, valor_fixo__isnull=False)),
                name="regra_percentual_ou_fixo",
            ),
            models.CheckConstraint(condition=Q(percentual__isnull=True) | (Q(percentual__gte=0) & Q(percentual__lte=100)), name="regra_percentual_valido"),
        ]


class Repasse(ModeloBase):
    class Status(models.TextChoices):
        A_PAGAR = "a_pagar", "A pagar"
        PAGO = "pago", "Pago"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="repasses")
    lancamento = models.OneToOneField(Lancamento, on_delete=models.CASCADE, related_name="repasse")
    competencia = models.DateField(help_text="Primeiro dia do mês em que o dinheiro entrou.")
    base = models.DecimalField(max_digits=10, decimal_places=2, help_text="Valor efetivamente recebido.")
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.A_PAGAR)
    pago_em = models.DateTimeField(null=True, blank=True)
    pago_por_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-competencia", "-criado_em"]


class CobrancaRecorrente(ModeloBase):
    """Aluguel de sala: cobranca mensal a parte, sem repasse por atendimento."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.CASCADE, related_name="+")
    descricao = models.CharField(max_length=120, default="Aluguel de sala")
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    dia_vencimento = models.PositiveSmallIntegerField(default=10, help_text="Dia do mês (1 a 28).")
    ativa = models.BooleanField(default=True)
    ultima_competencia = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(dia_vencimento__gte=1) & Q(dia_vencimento__lte=28), name="dia_vencimento_valido")]
