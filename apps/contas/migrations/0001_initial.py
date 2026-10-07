import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import apps.contas.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("plataforma", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Usuario",
            fields=[
                ("password", models.CharField(max_length=128, verbose_name="password")),
                ("last_login", models.DateTimeField(blank=True, null=True, verbose_name="last login")),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text="Designates that this user has all permissions without explicitly assigning them.",
                        verbose_name="superuser status",
                    ),
                ),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("nome", models.CharField(max_length=160)),
                ("is_active", models.BooleanField(default=True, verbose_name="ativo")),
                ("is_staff", models.BooleanField(default=False, verbose_name="operador da plataforma")),
                ("segundo_fator_segredo_cifrado", models.TextField(blank=True)),
                ("segundo_fator_ativo", models.BooleanField(default=False)),
                ("falhas_login", models.PositiveIntegerField(default=0)),
                ("bloqueado_ate", models.DateTimeField(blank=True, null=True)),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "The groups this user belongs to. A user will get all permissions granted to each of "
                            "their groups."
                        ),
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
            ],
            options={"abstract": False},
            managers=[("objects", apps.contas.models.UsuarioManager())],
        ),
        migrations.CreateModel(
            name="Profissional",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "tipo",
                    models.CharField(
                        choices=[("medico", "Médico (psiquiatra)"), ("psicologo", "Psicólogo")], max_length=20
                    ),
                ),
                ("conselho", models.CharField(choices=[("CRM", "CRM"), ("CRP", "CRP")], max_length=3)),
                ("numero", models.CharField(max_length=20, verbose_name="número de registro")),
                ("uf", models.CharField(max_length=2)),
                ("validado_em", models.DateTimeField(blank=True, null=True)),
                ("ativo", models.BooleanField(default=True)),
                (
                    "usuario",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profissional",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="profissional",
            constraint=models.UniqueConstraint(fields=("conselho", "numero", "uf"), name="registro_conselho_unico"),
        ),
        migrations.AddConstraint(
            model_name="profissional",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("conselho", "CRM"), ("tipo", "medico")),
                    models.Q(("conselho", "CRP"), ("tipo", "psicologo")),
                    _connector="OR",
                ),
                name="conselho_compativel_com_tipo",
            ),
        ),
        migrations.CreateModel(
            name="Vinculo",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "perfil",
                    models.CharField(
                        choices=[
                            ("profissional", "Profissional"),
                            ("assistente", "Assistente"),
                            ("admin", "Administrador"),
                        ],
                        max_length=20,
                    ),
                ),
                ("ativo", models.BooleanField(default=True)),
                (
                    "consultorio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vinculos",
                        to="plataforma.consultorio",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vinculos",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="vinculo",
            constraint=models.UniqueConstraint(fields=("usuario", "consultorio"), name="vinculo_unico"),
        ),
        migrations.CreateModel(
            name="SessaoDispositivo",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("session_key", models.CharField(max_length=40, unique=True)),
                ("dispositivo", models.CharField(blank=True, max_length=200)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("ultimo_uso", models.DateTimeField(default=django.utils.timezone.now)),
                ("revogada_em", models.DateTimeField(blank=True, null=True)),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sessoes_dispositivo",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-ultimo_uso"]},
        ),
    ]
