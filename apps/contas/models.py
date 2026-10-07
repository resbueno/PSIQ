from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core import cripto
from apps.core.models import ModeloBase


class Perfil(models.TextChoices):
    PROFISSIONAL = "profissional", "Profissional"
    ASSISTENTE = "assistente", "Assistente"
    ADMIN = "admin", "Administrador"


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def _criar(self, email, password, **extra):
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        usuario = self.model(email=self.normalize_email(email).lower(), **extra)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._criar(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._criar(email, password, **extra)


class Usuario(ModeloBase, AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    nome = models.CharField(max_length=160)
    is_active = models.BooleanField("ativo", default=True)
    is_staff = models.BooleanField("operador da plataforma", default=False)

    segundo_fator_segredo_cifrado = models.TextField(blank=True)
    segundo_fator_ativo = models.BooleanField(default=False)
    ultimo_passo_totp = models.BigIntegerField(null=True, blank=True, help_text="Ultimo passo de 30 s aceito (anti-reuso do codigo).")

    falhas_login = models.PositiveIntegerField(default=0)
    bloqueado_ate = models.DateTimeField(null=True, blank=True)

    objects = UsuarioManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nome"]

    def __str__(self):
        return self.email

    # --- segundo fator ---
    def definir_segredo_2fa(self, segredo: str):
        self.segundo_fator_segredo_cifrado = cripto.cifrar(segredo)

    @property
    def segredo_2fa(self) -> str:
        return cripto.decifrar(self.segundo_fator_segredo_cifrado) if self.segundo_fator_segredo_cifrado else ""

    # --- bloqueio progressivo ---
    @property
    def bloqueado(self) -> bool:
        return bool(self.bloqueado_ate and self.bloqueado_ate > timezone.now())

    def registrar_falha_login(self):
        self.falhas_login += 1
        excesso = self.falhas_login - settings.MEUPSIQ_LOGIN_FALHAS_ANTES_DO_BLOQUEIO
        if excesso >= 0:
            minutos = min(2**excesso, settings.MEUPSIQ_LOGIN_BLOQUEIO_MAXIMO_MINUTOS)
            self.bloqueado_ate = timezone.now() + timedelta(minutes=minutos)
        self.save(update_fields=["falhas_login", "bloqueado_ate", "atualizado_em"])

    def registrar_sucesso_login(self):
        if self.falhas_login or self.bloqueado_ate:
            self.falhas_login = 0
            self.bloqueado_ate = None
            self.save(update_fields=["falhas_login", "bloqueado_ate", "atualizado_em"])


class Profissional(ModeloBase):
    class Tipo(models.TextChoices):
        MEDICO = "medico", "Médico (psiquiatra)"
        PSICOLOGO = "psicologo", "Psicólogo"

    class Conselho(models.TextChoices):
        CRM = "CRM", "CRM"
        CRP = "CRP", "CRP"

    usuario = models.OneToOneField(Usuario, on_delete=models.CASCADE, related_name="profissional")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    conselho = models.CharField(max_length=3, choices=Conselho.choices)
    numero = models.CharField("número de registro", max_length=20)
    uf = models.CharField(max_length=2)
    validado_em = models.DateTimeField(null=True, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["conselho", "numero", "uf"], name="registro_conselho_unico"),
            models.CheckConstraint(
                condition=(models.Q(tipo="medico", conselho="CRM") | models.Q(tipo="psicologo", conselho="CRP")),
                name="conselho_compativel_com_tipo",
            ),
        ]

    def __str__(self):
        return f"{self.usuario.nome} ({self.conselho} {self.numero}/{self.uf})"

    @property
    def validado(self) -> bool:
        return self.validado_em is not None


class Vinculo(ModeloBase):
    """Liga um usuario a um consultorio com um perfil. Tabela sob RLS."""

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="vinculos")
    consultorio = models.ForeignKey("plataforma.Consultorio", on_delete=models.CASCADE, related_name="vinculos")
    perfil = models.CharField(max_length=20, choices=Perfil.choices)
    ativo = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "consultorio"], name="vinculo_unico")]

    def __str__(self):
        return f"{self.usuario} em {self.consultorio} como {self.perfil}"


class SessaoDispositivo(ModeloBase):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="sessoes_dispositivo")
    session_key = models.CharField(max_length=40, unique=True)
    dispositivo = models.CharField(max_length=200, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    ultimo_uso = models.DateTimeField(default=timezone.now)
    revogada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-ultimo_uso"]

    def __str__(self):
        return f"{self.usuario} · {self.dispositivo or 'dispositivo'}"
