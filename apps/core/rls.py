"""Ajudantes de SQL para Row Level Security.

Toda tabela de negocio com `consultorio_id` deve chamar `sql_habilitar_rls` em uma migracao.
O contexto vem de `app.consultorio_id`, definido por `apps.core.tenancy`. Sem contexto,
nenhuma linha e visivel (falha fechada). FORCE faz a regra valer tambem para o dono da tabela.
"""


def sql_habilitar_rls(tabela, coluna="consultorio_id"):
    return [
        f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY;',
        f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY;',
        (
            f'CREATE POLICY isolamento_consultorio ON "{tabela}" '
            f"USING ({coluna} = psiq_consultorio_atual()) "
            f"WITH CHECK ({coluna} = psiq_consultorio_atual());"
        ),
    ]


def sql_desabilitar_rls(tabela):
    return [
        f'DROP POLICY IF EXISTS isolamento_consultorio ON "{tabela}";',
        f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY;',
        f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY;',
    ]
