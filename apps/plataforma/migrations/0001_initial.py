import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Plano",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("nome", models.CharField(max_length=80, unique=True)),
                ("limite_pacientes_ativos", models.PositiveIntegerField()),
                ("limite_profissionais", models.PositiveIntegerField()),
                ("preco", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "tipo_cobranca",
                    models.CharField(
                        choices=[("consultorio", "Por consultório"), ("profissional", "Por profissional")],
                        default="consultorio",
                        max_length=20,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="Consultorio",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("nome", models.CharField(max_length=160)),
                ("documento", models.CharField(blank=True, max_length=18, verbose_name="CNPJ ou CPF")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ativo", "Ativo"),
                            ("carencia", "Em carência"),
                            ("somente_leitura", "Somente leitura"),
                            ("encerrado", "Encerrado"),
                        ],
                        default="ativo",
                        max_length=20,
                    ),
                ),
                ("fuso", models.CharField(default="America/Sao_Paulo", max_length=64)),
                ("politica_cancelamento_horas", models.PositiveIntegerField(default=24)),
                ("encerrado_em", models.DateTimeField(blank=True, null=True)),
                (
                    "plano",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="consultorios",
                        to="plataforma.plano",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
    ]
