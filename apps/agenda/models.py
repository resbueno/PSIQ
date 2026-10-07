from django.db import models
from django.db.models import Q

from apps.core.models import ModeloBase


class TipoAtendimento(models.TextChoices):
    PRESENCIAL = "presencial", "Presencial"
    ONLINE = "online", "Online"


class AgendaRegra(ModeloBase):
    """Janela semanal em que o profissional atende (base para listar horarios livres)."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.CASCADE, related_name="regras_agenda")
    dia_semana = models.PositiveSmallIntegerField(choices=[(0, "Segunda"), (1, "Terça"), (2, "Quarta"), (3, "Quinta"), (4, "Sexta"), (5, "Sábado"), (6, "Domingo")])
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()
    duracao_minutos = models.PositiveSmallIntegerField(default=50)

    class Meta:
        ordering = ["dia_semana", "hora_inicio"]
        constraints = [
            models.CheckConstraint(condition=Q(hora_fim__gt=models.F("hora_inicio")), name="regra_fim_depois_do_inicio"),
        ]


class SerieRecorrencia(ModeloBase):
    """Metadados de uma serie. Cada ocorrencia e uma Consulta propria: alterar ou cancelar uma nao apaga a serie."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="series")
    intervalo_semanas = models.PositiveSmallIntegerField(default=1)
    repeticoes = models.PositiveSmallIntegerField()
    encerrada_em = models.DateTimeField(null=True, blank=True)


class Consulta(ModeloBase):
    class Status(models.TextChoices):
        SOLICITADA = "solicitada", "Solicitada"
        AGENDADA = "agendada", "Agendada"
        CONFIRMADA = "confirmada", "Confirmada"
        REALIZADA = "realizada", "Realizada"
        CANCELADA = "cancelada", "Cancelada"
        FALTOU = "faltou", "Faltou"

    class CanceladoPor(models.TextChoices):
        EQUIPE = "equipe", "Consultório"
        PACIENTE = "paciente", "Paciente"

    ATIVAS = (Status.SOLICITADA, Status.AGENDADA, Status.CONFIRMADA)

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="consultas")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="consultas")
    paciente = models.ForeignKey("pacientes.Paciente", null=True, blank=True, on_delete=models.PROTECT, related_name="consultas")
    grupo = models.ForeignKey("pacientes.GrupoAtendimento", null=True, blank=True, on_delete=models.PROTECT, related_name="consultas")
    inicio = models.DateTimeField(db_index=True)
    fim = models.DateTimeField()
    tipo = models.CharField(max_length=12, choices=TipoAtendimento.choices, default=TipoAtendimento.PRESENCIAL)
    link_online = models.URLField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AGENDADA)
    primeira_consulta = models.BooleanField(default=False)
    serie = models.ForeignKey(SerieRecorrencia, null=True, blank=True, on_delete=models.SET_NULL, related_name="consultas")
    cancelada_em = models.DateTimeField(null=True, blank=True)
    cancelada_por = models.CharField(max_length=10, choices=CanceladoPor.choices, blank=True)
    falta_tardia = models.BooleanField(default=False, help_text="Cancelada pelo paciente dentro do prazo mínimo.")

    class Meta:
        ordering = ["inicio"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(paciente__isnull=False, grupo__isnull=True) | Q(paciente__isnull=True, grupo__isnull=False)
                ),
                name="consulta_paciente_ou_grupo",
            ),
            models.CheckConstraint(condition=Q(fim__gt=models.F("inicio")), name="consulta_fim_depois_do_inicio"),
        ]
        indexes = [models.Index(fields=["profissional", "inicio"], name="consulta_prof_inicio_idx")]

    def __str__(self):
        return f"{self.inicio:%d/%m/%Y %H:%M}"

    @property
    def ativa(self):
        return self.status in self.ATIVAS


class SolicitacaoHorario(ModeloBase):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        APROVADA = "aprovada", "Aprovada"
        RECUSADA = "recusada", "Recusada"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.ForeignKey("pacientes.Paciente", on_delete=models.CASCADE, related_name="solicitacoes")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.CASCADE, related_name="solicitacoes")
    horario_desejado = models.DateTimeField()
    tipo = models.CharField(max_length=12, choices=TipoAtendimento.choices, default=TipoAtendimento.PRESENCIAL)
    observacao = models.CharField(max_length=300, blank=True, help_text="Não registre informação clínica aqui.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    consulta = models.OneToOneField(Consulta, null=True, blank=True, on_delete=models.SET_NULL, related_name="solicitacao")
    decidida_por_id = models.UUIDField(null=True, blank=True)
    decidida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["horario_desejado"]


class Aviso(ModeloBase):
    class Canal(models.TextChoices):
        EMAIL = "email", "E-mail"
        PUSH = "push", "Notificação no aparelho"

    class Tipo(models.TextChoices):
        CONFIRMACAO = "confirmacao", "Confirmação"
        LEMBRETE = "lembrete", "Lembrete"
        REMARCACAO = "remarcacao", "Remarcação"
        CANCELAMENTO = "cancelamento", "Cancelamento"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ENVIADO = "enviado", "Enviado"
        FALHOU = "falhou", "Falhou"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    consulta = models.ForeignKey(Consulta, on_delete=models.CASCADE, related_name="avisos")
    canal = models.CharField(max_length=10, choices=Canal.choices)
    tipo = models.CharField(max_length=14, choices=Tipo.choices)
    destinatario = models.CharField(max_length=254)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    enviado_em = models.DateTimeField(null=True, blank=True)
    erro = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-criado_em"]
