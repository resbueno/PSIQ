from django.db import migrations

CRIAR = [
    """
    CREATE FUNCTION psiq_consultorio_atual() RETURNS uuid
    LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.consultorio_id', true), '')::uuid $$;
    """,
    """
    CREATE FUNCTION psiq_usuario_atual() RETURNS uuid
    LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.usuario_id', true), '')::uuid $$;
    """,
    """
    CREATE FUNCTION psiq_bloquear_alteracao() RETURNS trigger
    LANGUAGE plpgsql AS
    $$ BEGIN
        RAISE EXCEPTION USING MESSAGE = 'tabela append-only: ' || TG_TABLE_NAME;
    END; $$;
    """,
]

REMOVER = [
    "DROP FUNCTION psiq_bloquear_alteracao();",
    "DROP FUNCTION psiq_usuario_atual();",
    "DROP FUNCTION psiq_consultorio_atual();",
]


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunSQL(sql=CRIAR, reverse_sql=REMOVER)]
