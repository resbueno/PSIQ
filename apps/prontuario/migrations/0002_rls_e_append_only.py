from django.db import migrations

from apps.core.rls import sql_desabilitar_rls, sql_habilitar_rls

TABELAS = [
    "prontuario_prontuario",
    "prontuario_registroclinico",
    "prontuario_registroversao",
    "prontuario_anexo",
    "prontuario_modelodocumento",
    "prontuario_documento",
    "prontuario_liberacaoleitura",
    "prontuario_consentimentopaciente",
    "prontuario_delegacao",
]

# Versao de registro: nunca UPDATE; DELETE so dentro da exclusao antecipada autorizada (flag local a transacao).
FUNCAO = """
CREATE FUNCTION psiq_proteger_versao() RETURNS trigger
LANGUAGE plpgsql AS
$$ BEGIN
    IF TG_OP = 'DELETE' AND coalesce(current_setting('app.exclusao_autorizada', true), '') = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION USING MESSAGE = 'versao de registro clinico e append-only';
END; $$;
"""
TRIGGER = (
    'CREATE TRIGGER versao_append_only BEFORE UPDATE OR DELETE ON "prontuario_registroversao" '
    "FOR EACH ROW EXECUTE FUNCTION psiq_proteger_versao();"
)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_funcoes_rls"),
        ("prontuario", "0001_initial"),
    ]
    operations = [
        migrations.RunSQL(
            sql=[comando for tabela in TABELAS for comando in sql_habilitar_rls(tabela)] + [FUNCAO, TRIGGER],
            reverse_sql=[
                'DROP TRIGGER IF EXISTS versao_append_only ON "prontuario_registroversao";',
                "DROP FUNCTION IF EXISTS psiq_proteger_versao();",
            ]
            + [comando for tabela in TABELAS for comando in sql_desabilitar_rls(tabela)],
        )
    ]
