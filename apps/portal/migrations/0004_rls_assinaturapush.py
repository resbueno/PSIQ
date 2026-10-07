from django.db import migrations

from apps.core.rls import sql_desabilitar_rls, sql_habilitar_rls

TABELA = "portal_assinaturapush"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_funcoes_rls"),
        ("portal", "0003_assinaturapush"),
    ]
    operations = [migrations.RunSQL(sql=sql_habilitar_rls(TABELA), reverse_sql=sql_desabilitar_rls(TABELA))]
