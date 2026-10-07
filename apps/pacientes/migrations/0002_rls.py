from django.db import migrations

from apps.core.rls import sql_desabilitar_rls, sql_habilitar_rls

TABELAS = [
    "pacientes_paciente",
    "pacientes_responsavellegal",
    "pacientes_pagador",
    "pacientes_grupoatendimento",
    "pacientes_participantegrupo",
    "pacientes_profissionalpaciente",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_funcoes_rls"),
        ("pacientes", "0001_initial"),
    ]
    operations = [
        migrations.RunSQL(
            sql=[comando for tabela in TABELAS for comando in sql_habilitar_rls(tabela)],
            reverse_sql=[comando for tabela in TABELAS for comando in sql_desabilitar_rls(tabela)],
        )
    ]
