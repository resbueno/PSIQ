import uuid

from django.db import migrations, models

TABELA = "auditoria_auditoria"

APLICAR = [
    f'ALTER TABLE "{TABELA}" ENABLE ROW LEVEL SECURITY;',
    f'ALTER TABLE "{TABELA}" FORCE ROW LEVEL SECURITY;',
    # Cada consultorio le so a propria auditoria. Eventos sem consultorio (falha de login) nao sao lidos pelo app.
    f'CREATE POLICY ler_auditoria ON "{TABELA}" FOR SELECT USING (consultorio_id = psiq_consultorio_atual());',
    f'CREATE POLICY inserir_auditoria ON "{TABELA}" FOR INSERT '
    "WITH CHECK (consultorio_id IS NULL OR consultorio_id = psiq_consultorio_atual());",
    f'CREATE TRIGGER auditoria_append_only BEFORE UPDATE OR DELETE ON "{TABELA}" '
    "FOR EACH ROW EXECUTE FUNCTION psiq_bloquear_alteracao();",
]

REVERTER = [
    f'DROP TRIGGER IF EXISTS auditoria_append_only ON "{TABELA}";',
    f'DROP POLICY IF EXISTS inserir_auditoria ON "{TABELA}";',
    f'DROP POLICY IF EXISTS ler_auditoria ON "{TABELA}";',
    f'ALTER TABLE "{TABELA}" NO FORCE ROW LEVEL SECURITY;',
    f'ALTER TABLE "{TABELA}" DISABLE ROW LEVEL SECURITY;',
]


class Migration(migrations.Migration):
    initial = True
    dependencies = [("core", "0001_funcoes_rls")]

    operations = [
        migrations.CreateModel(
            name="Auditoria",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("consultorio_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("usuario_id", models.UUIDField(blank=True, null=True)),
                ("acao", models.CharField(max_length=60)),
                ("objeto", models.CharField(blank=True, max_length=60)),
                ("objeto_id", models.CharField(blank=True, max_length=64)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("dispositivo", models.CharField(blank=True, max_length=200)),
                ("detalhe", models.JSONField(blank=True, default=dict)),
                ("criado_em", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-criado_em"]},
        ),
        migrations.RunSQL(sql=APLICAR, reverse_sql=REVERTER),
    ]
