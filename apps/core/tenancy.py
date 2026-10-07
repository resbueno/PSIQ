"""Contexto de consultorio/usuario na conexao do banco, base das politicas de RLS."""

from contextlib import contextmanager

from django.db import connection

_CONSULTORIO = "app.consultorio_id"
_USUARIO = "app.usuario_id"


def definir_contexto(consultorio_id=None, usuario_id=None):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config(%s, %s, false), set_config(%s, %s, false)",
            [_CONSULTORIO, str(consultorio_id or ""), _USUARIO, str(usuario_id or "")],
        )


def limpar_contexto():
    definir_contexto(None, None)


def ler_contexto():
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting(%s, true), current_setting(%s, true)", [_CONSULTORIO, _USUARIO])
        consultorio, usuario = cur.fetchone()
    return (consultorio or None, usuario or None)


@contextmanager
def contexto(consultorio_id=None, usuario_id=None):
    """Define o contexto dentro do bloco e restaura o anterior ao sair."""
    anterior = ler_contexto()
    definir_contexto(consultorio_id, usuario_id)
    try:
        yield
    finally:
        definir_contexto(*anterior)
