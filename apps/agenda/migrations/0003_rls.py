from django.db import migrations

from apps.core.rls import sql_desabilitar_rls, sql_habilitar_rls

TABELAS = [
    "agenda_agendaregra",
    "agenda_serierecorrencia",
    "agenda_consulta",
    "agenda_solicitacaohorario",
    "agenda_aviso",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_funcoes_rls"),
        ("agenda", "0002_initial"),
    ]
    operations = [
        migrations.RunSQL(
            sql=[comando for tabela in TABELAS for comando in sql_habilitar_rls(tabela)],
            reverse_sql=[comando for tabela in TABELAS for comando in sql_desabilitar_rls(tabela)],
        )
    ]
