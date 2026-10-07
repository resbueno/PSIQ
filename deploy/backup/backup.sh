#!/usr/bin/env bash
# Backup diario do PSIQ (homologacao): dump do PostgreSQL + volume de anexos, cifrado com a chave PUBLICA
# (o servidor consegue cifrar, mas nao decifrar: a chave privada fica fora dele).
# A chave mestra (PSIQ_CHAVE_MESTRA) NAO entra no pacote: guarde-a a parte.
# Ao terminar com sucesso atualiza o marcador que `manage.py verificar_saude` vigia.
set -euo pipefail

RAIZ="${PSIQ_RAIZ:-/psiq}"
DEST="$RAIZ/backup/dados"
CERT="$RAIZ/backup/backup-publico.pem"
MARCADOR="$RAIZ/estado/ultimo-backup-ok"
MANTER="${MANTER:-14}"
COMPOSE="docker compose -p psiq -f $RAIZ/app/deploy/docker-compose.homolog.yml -f $RAIZ/app/deploy/docker-compose.homolog.gateway.yml --env-file $RAIZ/.env"

[ -f "$CERT" ] || { echo "Falta a chave publica de backup: $CERT" >&2; exit 1; }
umask 077
TS="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$DEST" "$(dirname "$MARCADOR")"

$COMPOSE exec -T db pg_dump -U postgres -Fc psiq > "$TMP/banco.dump"
$COMPOSE exec -T psiq-web tar czf - -C /data/anexos . > "$TMP/anexos.tgz"
[ -s "$TMP/banco.dump" ] || { echo "Dump vazio" >&2; exit 1; }

tar cf "$TMP/pacote.tar" -C "$TMP" banco.dump anexos.tgz
SAIDA="$DEST/psiq-$TS.tar.cms"
openssl cms -encrypt -binary -stream -aes256 -outform DER -in "$TMP/pacote.tar" -out "$SAIDA" "$CERT"
(cd "$DEST" && sha256sum "$(basename "$SAIDA")" > "$(basename "$SAIDA").sha256")

# retencao
ls -1t "$DEST"/psiq-*.tar.cms 2>/dev/null | tail -n +"$((MANTER + 1))" | while read -r antigo; do rm -f "$antigo" "$antigo.sha256"; done

touch "$MARCADOR"
echo "Backup ok: $SAIDA ($(du -h "$SAIDA" | cut -f1))"
