from datetime import date

from django.db import models
from django.db.models import Q

from apps.core.models import ModeloBase

PRAZO_GUARDA_ANOS = {"medico": 20, "psicologo": 5}


class ChaveDados(ModeloBase):
    """Chave de dados do profissional, cifrada pela chave mestra. Sem consultorio_id: acompanha o profissional."""

    profissional = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="chaves")
    chave_cifrada = models.TextField()
    ativa = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profissional"], condition=Q(ativa=True), name="uma_chave_ativa_por_profissional")
        ]


class Prontuario(ModeloBase):
    class Tipo(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        CONJUNTO = "conjunto", "Conjunto (casal ou família)"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.ForeignKey("pacientes.Paciente", null=True, blank=True, on_delete=models.PROTECT, related_name="prontuarios")
    grupo = models.ForeignKey("pacientes.GrupoAtendimento", null=True, blank=True, on_delete=models.PROTECT, related_name="prontuarios")
    profissional = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="prontuarios", help_text="Dono do prontuário.")
    contrato = models.ForeignKey("plataforma.Contrato", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.INDIVIDUAL)
    ultimo_registro_em = models.DateTimeField(null=True, blank=True)
    excluido_em = models.DateTimeField(null=True, blank=True)
    excluido_por_id = models.UUIDField(null=True, blank=True)
    exclusao_motivo = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(paciente__isnull=False, grupo__isnull=True) | Q(paciente__isnull=True, grupo__isnull=False),
                name="prontuario_paciente_ou_grupo",
            ),
            models.UniqueConstraint(
                fields=["paciente", "profissional"], condition=Q(excluido_em__isnull=True, paciente__isnull=False),
                name="prontuario_unico_paciente_profissional",
            ),
        ]

    def __str__(self):
        return f"Prontuário de {self.paciente or self.grupo}"

    @property
    def prazo_guarda_ate(self) -> date:
        """Medico: 20 anos; psicologo: 5 anos, a contar do ultimo registro."""
        base = (self.ultimo_registro_em or self.criado_em).date()
        anos = PRAZO_GUARDA_ANOS[self.profissional.tipo]
        try:
            return base.replace(year=base.year + anos)
        except ValueError:  # 29/02
            return base.replace(year=base.year + anos, day=28)

    @property
    def em_prazo_de_guarda(self) -> bool:
        return date.today() < self.prazo_guarda_ate


class RegistroClinico(ModeloBase):
    class Tipo(models.TextChoices):
        EVOLUCAO = "evolucao", "Evolução"
        ANAMNESE = "anamnese", "Anamnese"
        PRESCRICAO = "prescricao_registro", "Registro de prescrição"
        OUTRO = "outro", "Outro"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    prontuario = models.ForeignKey(Prontuario, on_delete=models.CASCADE, related_name="registros")
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.EVOLUCAO)
    consulta = models.ForeignKey("agenda.Consulta", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-criado_em"]

    def versao_atual(self):
        return self.versoes.order_by("-numero").first()


class RegistroVersao(ModeloBase):
    """Append-only: o banco bloqueia UPDATE e so permite DELETE na exclusao antecipada autorizada."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    registro = models.ForeignKey(RegistroClinico, on_delete=models.CASCADE, related_name="versoes")
    numero = models.PositiveIntegerField()
    conteudo_cifrado = models.TextField()
    cid_cifrado = models.TextField(blank=True)
    chave = models.ForeignKey(ChaveDados, on_delete=models.PROTECT, related_name="+")
    autor_id = models.UUIDField()

    class Meta:
        ordering = ["numero"]
        constraints = [models.UniqueConstraint(fields=["registro", "numero"], name="versao_unica")]


class Anexo(ModeloBase):
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    prontuario = models.ForeignKey(Prontuario, on_delete=models.CASCADE, related_name="anexos")
    nome = models.CharField(max_length=200)
    caminho = models.CharField(max_length=300)
    tamanho = models.PositiveIntegerField()
    hash_sha256 = models.CharField(max_length=64)
    importado_historico = models.BooleanField(default=False)
    chave = models.ForeignKey(ChaveDados, on_delete=models.PROTECT, related_name="+")
    enviado_por_id = models.UUIDField()

    class Meta:
        ordering = ["-criado_em"]


class ModeloDocumento(ModeloBase):
    class Tipo(models.TextChoices):
        COMPARECIMENTO = "comparecimento", "Declaração de comparecimento"
        DECLARACAO = "declaracao", "Declaração"
        ATESTADO = "atestado", "Atestado"
        RELATORIO = "relatorio", "Relatório"

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    titulo = models.CharField(max_length=120)
    texto_base = models.TextField()

    class Meta:
        ordering = ["titulo"]

    def __str__(self):
        return self.titulo


class Documento(ModeloBase):
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.ForeignKey("pacientes.Paciente", on_delete=models.PROTECT, related_name="documentos")
    prontuario = models.ForeignKey(Prontuario, null=True, blank=True, on_delete=models.CASCADE, related_name="documentos")
    modelo = models.ForeignKey(ModeloDocumento, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    titulo = models.CharField(max_length=160)
    caminho = models.CharField(max_length=300)
    hash_sha256 = models.CharField(max_length=64)
    chave = models.ForeignKey(ChaveDados, null=True, blank=True, on_delete=models.PROTECT, related_name="+", help_text="Vazio: cifrado pela chave mestra (documento sem conteudo clinico).")
    autor_id = models.UUIDField()
    liberado_ao_paciente = models.BooleanField(default=False)

    class Meta:
        ordering = ["-criado_em"]


class LiberacaoLeitura(ModeloBase):
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    prontuario = models.ForeignKey(Prontuario, on_delete=models.CASCADE, related_name="liberacoes")
    usuario = models.ForeignKey("contas.Usuario", on_delete=models.CASCADE, related_name="+")
    concedido_por_id = models.UUIDField()
    revogado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]


class ConsentimentoPaciente(ModeloBase):
    """Aceite eletronico do paciente para outro profissional ler o prontuario. Revogavel pelo mesmo caminho."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    paciente = models.ForeignKey("pacientes.Paciente", on_delete=models.PROTECT, related_name="consentimentos")
    profissional_origem = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="+")
    profissional_destino = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="+")
    escopo = models.CharField(max_length=20, default="leitura")
    termo_versao = models.CharField(max_length=10, blank=True)
    aceito_em = models.DateTimeField(null=True, blank=True)
    ip_aceite = models.GenericIPAddressField(null=True, blank=True)
    revogado_em = models.DateTimeField(null=True, blank=True)
    ip_revogacao = models.GenericIPAddressField(null=True, blank=True)

    @property
    def vigente(self) -> bool:
        return self.aceito_em is not None and self.revogado_em is None


class Delegacao(ModeloBase):
    """Mudanca de dono do prontuario (saida do profissional, fim de contrato). Registrada, nunca uma edicao."""

    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.PROTECT, related_name="+")
    prontuario = models.ForeignKey(Prontuario, on_delete=models.CASCADE, related_name="delegacoes")
    de = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="+")
    para = models.ForeignKey("contas.Profissional", on_delete=models.PROTECT, related_name="+")
    feita_por_id = models.UUIDField()
    motivo = models.CharField(max_length=300, blank=True)
