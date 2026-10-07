from django.db import migrations

from apps.core.rls import sql_desabilitar_rls, sql_habilitar_rls

TABELAS = ["portal_codigoacesso", "portal_solicitacaolgpd"]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_funcoes_rls"),
        ("portal", "0001_initial"),
    ]
    operations = [
        migrations.RunSQL(
            sql=[c for t in TABELAS for c in sql_habilitar_rls(t)],
            reverse_sql=[c for t in TABELAS for c in sql_desabilitar_rls(t)],
        )
    ]
