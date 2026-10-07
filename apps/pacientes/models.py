from datetime import date

from django.db import models
from django.db.models import Q

from apps.core.models import ModeloBase


class Paciente(ModeloBase):
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="pacientes")
    nome = models.CharField(max_length=160)
    cpf = models.CharField(max_length=11, blank=True, help_text="Somente números. Pode ficar vazio (ex.: criança).")
    nascimento = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    convenio = models.CharField("convênio", max_length=80, blank=True)
    carteirinha = models.CharField(max_length=40, blank=True)
    ativo = models.BooleanField(default=True)
    exige_aprovacao = models.BooleanField(
        "retornos também exigem aprovação",
        default=False,
        help_text="A primeira consulta sempre passa por aprovação.",
    )
    ultimo_atendimento_em = models.DateTimeField(null=True, blank=True)
    mesclado_em = models.DateTimeField(null=True, blank=True)
    mesclado_com = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="mesclados")

    class Meta:
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["consultorio", "cpf"],
                condition=~Q(cpf="") & Q(mesclado_em__isnull=True),
                name="cpf_unico_por_consultorio",
            )
        ]

    def __str__(self):
        return self.nome

    @property
    def idade(self):
        if not self.nascimento:
            return None
        hoje = date.today()
        return hoje.year - self.nascimento.year - ((hoje.month, hoje.day) < (self.nascimento.month, self.nascimento.day))

    @property
    def menor_de_idade(self):
        return self.idade is not None and self.idade < 18


class ResponsavelLegal(ModeloBase):
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="responsaveis")
    nome = models.CharField(max_length=160)
    cpf = models.CharField(max_length=11, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    recebe_avisos = models.BooleanField(default=True)
    pode_pagar = models.BooleanField("é o pagador", default=False)

    def __str__(self):
        return self.nome


class Pagador(ModeloBase):
    """Quando o pagador e outra pessoa, o recibo sai no nome dele (nao ganha acesso clinico)."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.OneToOneField(Paciente, on_delete=models.CASCADE, related_name="pagador")
    nome = models.CharField(max_length=160)
    cpf = models.CharField(max_length=11)

    def __str__(self):
        return self.nome


class GrupoAtendimento(ModeloBase):
    class Tipo(models.TextChoices):
        CASAL = "casal", "Casal"
        FAMILIA = "familia", "Família"
        GRUPO = "grupo", "Grupo"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    nome = models.CharField(max_length=120)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class ParticipanteGrupo(ModeloBase):
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    grupo = models.ForeignKey(GrupoAtendimento, on_delete=models.CASCADE, related_name="participantes")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="participacoes")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["grupo", "paciente"], name="participante_unico")]


class ProfissionalPaciente(ModeloBase):
    """Define quais pacientes cada profissional enxerga ('seus pacientes')."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="profissionais")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.CASCADE, related_name="pacientes")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["paciente", "profissional"], name="profissional_paciente_unico")]
