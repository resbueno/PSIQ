from django.db import models

from apps.core import cripto
from apps.core.models import ModeloBase


class ContaCalendario(ModeloBase):
    """Conexao OAuth de um profissional com Google Calendar ou Outlook. Tokens ficam cifrados pela chave mestra."""

    class Provedor(models.TextChoices):
        GOOGLE = "google", "Google Calendar"
        MICROSOFT = "microsoft", "Outlook / Microsoft 365"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.CASCADE, related_name="contas_calendario")
    provedor = models.CharField(max_length=12, choices=Provedor.choices)
    email_conta = models.CharField(max_length=254, blank=True)
    token_acesso_cifrado = models.TextField(blank=True)
    token_refresh_cifrado = models.TextField(blank=True)
    expira_em = models.DateTimeField(null=True, blank=True)
    ativa = models.BooleanField(default=True)
    mostra_nome_paciente = models.BooleanField(
        "mostrar o nome do paciente no evento", default=False,
        help_text="Por padrão o evento externo mostra só 'Consulta' e o horário.",
    )
    ultima_sincronizacao = models.DateTimeField(null=True, blank=True)
    ultimo_erro = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["profissional", "provedor"], name="conta_calendario_unica")]

    def guardar_tokens(self, acesso, refresh, expira_em):
        self.token_acesso_cifrado = cripto.cifrar(acesso) if acesso else ""
        if refresh:  # nem toda renovacao devolve um refresh novo
            self.token_refresh_cifrado = cripto.cifrar(refresh)
        self.expira_em = expira_em

    @property
    def token_acesso(self) -> str:
        return cripto.decifrar(self.token_acesso_cifrado) if self.token_acesso_cifrado else ""

    @property
    def token_refresh(self) -> str:
        return cripto.decifrar(self.token_refresh_cifrado) if self.token_refresh_cifrado else ""


class EventoExterno(ModeloBase):
    """'meupsiq': evento que o MeuPSIQ criou no calendario externo. 'externo': compromisso pessoal que bloqueia horarios."""

    class Origem(models.TextChoices):
        PSIQ = "meupsiq", "Criado pelo MeuPSIQ"
        EXTERNO = "externo", "Compromisso externo"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    conta = models.ForeignKey(ContaCalendario, on_delete=models.CASCADE, related_name="eventos")
    origem = models.CharField(max_length=8, choices=Origem.choices)
    consulta = models.ForeignKey("agenda.Consulta", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    id_externo = models.CharField(max_length=300)
    titulo = models.CharField(max_length=200, blank=True, help_text="Titulo exportado (so eventos criados pelo MeuPSIQ).")
    inicio = models.DateTimeField()
    fim = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conta", "id_externo"], name="evento_externo_unico")]
        indexes = [models.Index(fields=["conta", "origem", "inicio"], name="evento_conta_origem_idx")]
