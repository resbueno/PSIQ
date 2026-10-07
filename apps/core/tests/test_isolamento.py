"""Isolamento entre consultorios no nivel do banco (RLS), com o papel `app`."""

import pytest
from django.db import DatabaseError, connection, transaction

from apps.auditoria.models import Auditoria
from apps.contas.models import Perfil, Vinculo
from apps.core.tenancy import contexto, definir_contexto, ler_contexto

pytestmark = pytest.mark.django_db


def test_papel_do_banco_nao_ignora_rls():
    """Se este teste falha, o RLS esta sendo ignorado e os demais testes de isolamento nao valem."""
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        superusuario, ignora_rls = cur.fetchone()
    assert not superusuario and not ignora_rls, "Use um papel sem SUPERUSER/BYPASSRLS (veja scripts/db/init-local.sql)."


@pytest.fixture
def dois_consultorios(criar_consultorio, criar_usuario):
    a, b = criar_consultorio("A"), criar_consultorio("B")
    ua = criar_usuario("a@exemplo.com", a, Perfil.ADMIN)
    ub = criar_usuario("b@exemplo.com", b, Perfil.ADMIN)
    return a, b, ua, ub


def test_sem_contexto_nenhuma_linha_e_visivel(dois_consultorios):
    definir_contexto(None, None)
    assert Vinculo.objects.count() == 0


def test_consultorio_so_ve_os_proprios_vinculos(dois_consultorios):
    a, b, ua, ub = dois_consultorios
    with contexto(consultorio_id=a.pk):
        assert set(Vinculo.objects.values_list("usuario_id", flat=True)) == {ua.pk}
    with contexto(consultorio_id=b.pk):
        assert set(Vinculo.objects.values_list("usuario_id", flat=True)) == {ub.pk}


def test_usuario_ve_os_proprios_vinculos_sem_consultorio(dois_consultorios):
    a, b, ua, ub = dois_consultorios
    with contexto(usuario_id=ua.pk):
        assert [v.consultorio_id for v in Vinculo.objects.all()] == [a.pk]


def test_nao_grava_vinculo_em_outro_consultorio(dois_consultorios):
    a, b, ua, ub = dois_consultorios
    with contexto(consultorio_id=a.pk):
        with pytest.raises(DatabaseError), transaction.atomic():
            Vinculo.objects.create(usuario=ua, consultorio=b, perfil=Perfil.ASSISTENTE)


def test_nao_altera_nem_apaga_vinculo_de_outro_consultorio(dois_consultorios):
    a, b, ua, ub = dois_consultorios
    with contexto(consultorio_id=a.pk, usuario_id=ua.pk):
        assert Vinculo.objects.filter(consultorio=b).update(ativo=False) == 0
        assert Vinculo.objects.filter(consultorio=b).delete()[0] == 0
    with contexto(consultorio_id=b.pk):
        assert Vinculo.objects.filter(ativo=True).count() == 1


def test_sql_direto_tambem_respeita_o_consultorio(dois_consultorios):
    a, b, ua, ub = dois_consultorios
    definir_contexto(consultorio_id=a.pk)
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM contas_vinculo")
        assert cur.fetchone()[0] == 1


def test_contexto_restaura_o_valor_anterior(dois_consultorios):
    a, b, *_ = dois_consultorios
    definir_contexto(consultorio_id=a.pk)
    with contexto(consultorio_id=b.pk):
        assert ler_contexto()[0] == str(b.pk)
    assert ler_contexto()[0] == str(a.pk)


# --- auditoria ---


def test_auditoria_isolada_por_consultorio(dois_consultorios):
    a, b, *_ = dois_consultorios
    with contexto(consultorio_id=a.pk):
        Auditoria.objects.create(consultorio_id=a.pk, acao="x")
    with contexto(consultorio_id=b.pk):
        Auditoria.objects.create(consultorio_id=b.pk, acao="y")
        assert list(Auditoria.objects.values_list("acao", flat=True)) == ["y"]


def test_auditoria_nao_grava_em_nome_de_outro_consultorio(dois_consultorios):
    a, b, *_ = dois_consultorios
    with contexto(consultorio_id=a.pk):
        with pytest.raises(DatabaseError), transaction.atomic():
            Auditoria.objects.create(consultorio_id=b.pk, acao="falso")


def test_auditoria_aceita_evento_sem_consultorio():
    definir_contexto(None, None)
    Auditoria.objects.create(consultorio_id=None, acao="login_falha")


def test_auditoria_nao_pode_ser_alterada_nem_apagada(dois_consultorios):
    """Sem politica de UPDATE/DELETE, o RLS filtra a linha: a operacao afeta 0 registros."""
    a, *_ = dois_consultorios
    with contexto(consultorio_id=a.pk):
        evento = Auditoria.objects.create(consultorio_id=a.pk, acao="x")
        assert Auditoria.objects.filter(pk=evento.pk).update(acao="adulterado") == 0
        assert Auditoria.objects.filter(pk=evento.pk).delete()[0] == 0
        assert Auditoria.objects.get(pk=evento.pk).acao == "x"


def test_trigger_bloqueia_alteracao_mesmo_sem_rls(dois_consultorios):
    """Segunda camada: se o RLS fosse desligado (dono da tabela), o trigger ainda barra UPDATE e DELETE."""
    a, *_ = dois_consultorios
    with contexto(consultorio_id=a.pk):
        evento = Auditoria.objects.create(consultorio_id=a.pk, acao="x")
    with connection.cursor() as cur:  # DDL e transacional no PostgreSQL: o teste desfaz tudo ao fim
        cur.execute("ALTER TABLE auditoria_auditoria NO FORCE ROW LEVEL SECURITY")
        cur.execute("ALTER TABLE auditoria_auditoria DISABLE ROW LEVEL SECURITY")
    with pytest.raises(DatabaseError), transaction.atomic():
        Auditoria.objects.filter(pk=evento.pk).update(acao="adulterado")
    with pytest.raises(DatabaseError), transaction.atomic():
        Auditoria.objects.filter(pk=evento.pk).delete()
