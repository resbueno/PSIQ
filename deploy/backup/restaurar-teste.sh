#!/usr/bin/env bash
# Teste de restauracao: decifra um backup e restaura num banco TEMPORARIO (psiq_restauro), sem tocar no banco real.
# Confere migracoes, RLS forcado, gatilhos append-only e conta registros. Apaga o banco temporario ao final.
# Uso:  restaurar-teste.sh /caminho/chave-privada.pem [arquivo.tar.cms]   (padrao: o backup mais recente)
set -euo pipefail

RAIZ="${PSIQ_RAIZ:-/psiq}"
CHAVE="${1:?Informe a chave privada de backup (fica fora do servidor)}"
ARQ="${2:-$(ls -1t "$RAIZ"/backup/dados/psiq-*.tar.cms | head -1)}"
COMPOSE="docker compose -p psiq -f $RAIZ/app/deploy/docker-compose.homolog.yml -f $RAIZ/app/deploy/docker-compose.homolog.gateway.yml --env-file $RAIZ/.env"
PSQL="$COMPOSE exec -T db psql -U postgres -v ON_ERROR_STOP=1 -At"

umask 077
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; $PSQL -c "DROP DATABASE IF EXISTS psiq_restauro" >/dev/null 2>&1 || true' EXIT

echo "Arquivo: $ARQ"
(cd "$(dirname "$ARQ")" && sha256sum -c "$(basename "$ARQ").sha256")
openssl cms -decrypt -binary -inform DER -in "$ARQ" -inkey "$CHAVE" -out "$TMP/pacote.tar"
tar xf "$TMP/pacote.tar" -C "$TMP"
tar tzf "$TMP/anexos.tgz" > /dev/null && echo "anexos.tgz integro ($(tar tzf "$TMP/anexos.tgz" | wc -l) entradas)"

$PSQL -c "DROP DATABASE IF EXISTS psiq_restauro" -c "CREATE DATABASE psiq_restauro OWNER psiq_app"
$COMPOSE exec -T db pg_restore -U postgres -d psiq_restauro --no-owner --role=psiq_app --exit-on-error < "$TMP/banco.dump"

echo "--- migracoes em dia no banco restaurado?"
$COMPOSE exec -T -e DB_NAME=psiq_restauro psiq-web python manage.py migrate --check && echo "sim"

echo "--- verificacoes de integridade"
$COMPOSE exec -T db psql -U postgres -d psiq_restauro -At <<'SQL'
select 'consultorios: ' || count(*) from plataforma_consultorio;
select 'usuarios: ' || count(*) from contas_usuario;
select 'tabelas com RLS forcado: ' || count(*) filter (where relrowsecurity and relforcerowsecurity) from pg_class where relkind = 'r' and relname like any (array['pacientes_%','agenda_%','prontuario_%','financeiro_%','portal_%','calendarios_%']) and relname <> 'prontuario_chavedados';
select 'gatilhos append-only: ' || count(*) from pg_trigger where tgname in ('auditoria_append_only', 'versao_append_only');
SQL
echo "RESTAURACAO OK"
