from django.db import migrations

TABELA = "contas_vinculo"

# O vinculo e visivel no consultorio da sessao OU ao proprio usuario (para escolher o consultorio apos o login).
# Escrita so e permitida dentro do consultorio da sessao.
APLICAR = [
    f'ALTER TABLE "{TABELA}" ENABLE ROW LEVEL SECURITY;',
    f'ALTER TABLE "{TABELA}" FORCE ROW LEVEL SECURITY;',
    f'CREATE POLICY ler_vinculo ON "{TABELA}" FOR SELECT '
    "USING (consultorio_id = psiq_consultorio_atual() OR usuario_id = psiq_usuario_atual());",
    f'CREATE POLICY inserir_vinculo ON "{TABELA}" FOR INSERT WITH CHECK (consultorio_id = psiq_consultorio_atual());',
    f'CREATE POLICY alterar_vinculo ON "{TABELA}" FOR UPDATE '
    "USING (consultorio_id = psiq_consultorio_atual()) WITH CHECK (consultorio_id = psiq_consultorio_atual());",
    f'CREATE POLICY apagar_vinculo ON "{TABELA}" FOR DELETE USING (consultorio_id = psiq_consultorio_atual());',
]

REVERTER = [
    f'DROP POLICY IF EXISTS ler_vinculo ON "{TABELA}";',
    f'DROP POLICY IF EXISTS inserir_vinculo ON "{TABELA}";',
    f'DROP POLICY IF EXISTS alterar_vinculo ON "{TABELA}";',
    f'DROP POLICY IF EXISTS apagar_vinculo ON "{TABELA}";',
    f'ALTER TABLE "{TABELA}" NO FORCE ROW LEVEL SECURITY;',
    f'ALTER TABLE "{TABELA}" DISABLE ROW LEVEL SECURITY;',
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_funcoes_rls"),
        ("contas", "0001_initial"),
    ]
    operations = [migrations.RunSQL(sql=APLICAR, reverse_sql=REVERTER)]
