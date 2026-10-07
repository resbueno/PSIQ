# Operação do PSIQ

Tarefas agendadas (cron do host ou `docker compose exec app ...`):

| Frequência | Comando | O que faz |
|---|---|---|
| a cada 15 min | `python manage.py enviar_lembretes` | lembrete das consultas das próximas 24 h |
| a cada 10 min | `python manage.py sincronizar_calendarios` | Google/Outlook em duas vias |
| a cada 10 min | `python manage.py verificar_saude` | alerta por e-mail: backup, avisos, sinais de ataque |
| diária | `python manage.py atualizar_inadimplencia` | aviso, carência de 15 dias, somente leitura |
| diária | `python manage.py gerar_cobrancas_recorrentes` | aluguel de sala do mês (não duplica) |
| trimestral | `python manage.py rotacionar_chaves` | nova chave de dados por profissional (as antigas continuam abrindo o histórico) |

## Backup (docs/02, seção 5)

- PostgreSQL com arquivamento contínuo de WAL (pgBackRest ou Barman) e snapshot diário da VPS. Metas: RPO 5 min, RTO 4 h.
- Anexos e recibos (`PSIQ_ANEXOS_DIR`) copiados para armazenamento S3-compatível **em outro provedor**, já cifrados.
- Backups cifrados com chave guardada fora da VPS. **A chave mestra (`PSIQ_CHAVE_MESTRA`) nunca vai no mesmo backup que o banco.**
- Ao terminar com sucesso, o job de backup atualiza o arquivo de `PSIQ_BACKUP_MARCADOR` (`touch`). Se passar de 26 h sem atualizar, `verificar_saude` alerta.
- Teste de restauração mensal, documentado. Restaure em homologação, rode `migrate` e os testes de isolamento.

## Chaves

- `PSIQ_CHAVE_MESTRA` aceita várias chaves separadas por vírgula (a primeira cifra, todas decifram). Para trocar: coloque a nova na frente (`nova,antiga`), rode `python manage.py rotacionar_chave_mestra` e só depois remova a antiga.
- Perder a chave mestra significa perder o acesso a prontuários, anexos, recibos e segredos de 2FA. Guarde cópias em dois lugares seguros e separados.

## Banco

- Três papéis (`scripts/db/init-producao.sql`): `psiq_migrator` (migrações), `psiq_app` (aplicação, sujeito ao RLS) e `psiq_backup` (somente leitura). Nenhum é superusuário nem tem BYPASSRLS.
- Migrações: `DB_USER=psiq_migrator python manage.py migrate`. Migração de dados em tabela com RLS precisa definir o contexto (`apps.core.tenancy.contexto`).

## Monitoramento

- `GET /saude/` responde 200 quando a aplicação alcança o banco. Aponte o monitor externo (uptime) para ele.
- `verificar_saude` cobre: backup parado, falhas de aviso, contas com muitas falhas de login, exportações em massa e acesso de suporte fora de autorização. Configure `PSIQ_ALERTA_EMAIL`.

## Antes do primeiro cliente (docs/06)

1. Revisão jurídica dos modelos de contrato, política, termo e plano de incidente por advogado especializado.
2. Backup restaurado com sucesso em homologação.
3. Plano de incidente testado (simulação).
4. Revisão de segurança dedicada: isolamento entre consultórios, permissões, dados em logs, dependências (`pip-audit` roda no CI).
5. Registro dos apps OAuth no Google e na Microsoft e revisão deles (calendários externos).
6. Provedor de e-mail transacional com SPF/DKIM e chaves VAPID para push.

## Homologação (VPS compartilhado, https://rbbrdevhomolog.duckdns.org/psiq/)

- Pasta `/psiq` (código em `app/`, segredos em `.env`, backups em `backup/dados/`). Contêineres `psiq-psiq-web-1` e `psiq-db-1`, projeto Docker `psiq`; só `127.0.0.1:8088` no host e o apelido `psiq_app` na rede do gateway.
- Gateway (`/root/gateway/nginx.conf`): blocos `/psiq/` (prefixo removido, upstream por variável para não derrubar o gateway se o PSIQ parar) e limite de taxa próprio (`psiqauth`, 20 req/min por IP) para login, 2FA, código do portal e links públicos. Antes de editar: backup do arquivo, gravar **sem trocar o inode** (`cat novo > nginx.conf`), `nginx -t` e só então `nginx -s reload`.
- Atualizar: `git -C /psiq/app pull` e `docker compose -p psiq -f app/deploy/docker-compose.homolog.yml -f app/deploy/docker-compose.homolog.gateway.yml --env-file .env up -d --build psiq-web` (+ `migrate`).
- Backup diário 02:30 (`deploy/backup/backup.sh`): dump + anexos, cifrado com a chave pública; **a chave privada não fica no servidor**. Restauração testada com `deploy/backup/restaurar-teste.sh <chave-privada>` (banco temporário, confere migrações, RLS e gatilhos). Falta copiar os backups para outro provedor.
- Cron do root (bloco `PSIQ-INICIO`/`PSIQ-FIM`): lembretes, calendários, saúde, inadimplência, cobranças recorrentes e backup.
