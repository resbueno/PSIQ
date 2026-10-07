from django.db import migrations

from apps.core.rls import sql_desabilitar_rls, sql_habilitar_rls

TABELA = "relatorios_exportacao"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_funcoes_rls"),
        ("relatorios", "0001_initial"),
    ]
    operations = [migrations.RunSQL(sql=sql_habilitar_rls(TABELA), reverse_sql=sql_desabilitar_rls(TABELA))]
