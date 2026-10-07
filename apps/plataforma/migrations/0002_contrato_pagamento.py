import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("plataforma", "0001_initial"),
        ("contas", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Contrato",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "tipo",
                    models.CharField(
                        choices=[("individual", "Individual"), ("consultorio", "Consultório")], max_length=20
                    ),
                ),
                ("vigencia_inicio", models.DateField()),
                ("vigencia_fim", models.DateField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("ativo", "Ativo"), ("encerrado", "Encerrado")], default="ativo", max_length=20
                    ),
                ),
                (
                    "consultorio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="contratos",
                        to="plataforma.consultorio",
                    ),
                ),
                (
                    "profissional",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="contratos",
                        to="contas.profissional",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="contrato",
            constraint=models.CheckConstraint(
                condition=models.Q(("tipo", "consultorio"), ("profissional__isnull", False), _connector="OR"),
                name="contrato_individual_exige_profissional",
            ),
        ),
        migrations.CreateModel(
            name="PagamentoPlataforma",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("competencia", models.DateField(help_text="Primeiro dia do mês de referência.")),
                ("valor", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "status",
                    models.CharField(
                        choices=[("pendente", "Pendente"), ("pago", "Pago")], default="pendente", max_length=20
                    ),
                ),
                (
                    "pago_em",
                    models.DateTimeField(blank=True, help_text="Marcado manualmente pelo operador.", null=True),
                ),
                (
                    "consultorio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pagamentos",
                        to="plataforma.consultorio",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="pagamentoplataforma",
            constraint=models.UniqueConstraint(
                fields=("consultorio", "competencia"), name="pagamento_unico_por_competencia"
            ),
        ),
    ]
