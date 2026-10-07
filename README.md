# PSIQ

Plataforma SaaS de gestão de agenda, pacientes, financeiro e prontuário para psicólogos e psiquiatras. Vários consultórios independentes, dados isolados por consultório.

A especificação fica na pasta `docs/`, mantida só localmente (fora do Git).

## Estado

| Etapa | Conteúdo | Situação |
|---|---|---|
| 1 | Base: consultórios, usuários, perfis, login + 2FA, dispositivos, auditoria, RLS | Pronta, testes verdes no CI |
| 2 | Pacientes e agenda, com avisos por e-mail | Pronta, testes verdes no CI (push, FullCalendar e HTMX pendentes) |
| 3 | Prontuário e documentos | A fazer |
| 4 | Financeiro, convênio e repasse | A fazer |
| 5 | Portal do paciente, teleconsulta, calendários | A fazer |
| 6 | Painel interno, cobrança, relatórios, importação | A fazer |

> Os testes rodam no GitHub Actions (PostgreSQL real). Este repositório foi desenvolvido sem Python local; o CI é o ambiente de verificação.

### O que a Etapa 1 entrega

- **Isolamento por consultório no banco (RLS).** Toda tabela de negócio tem `consultorio_id` e política de RLS (`FORCE`). A aplicação grava o consultório da sessão na conexão (`apps/core/tenancy.py`); sem contexto, nenhuma linha é visível. Ajudantes para novas tabelas em `apps/core/rls.py`.
- **Auditoria append-only** (`apps/auditoria`): trigger no banco bloqueia `UPDATE` e `DELETE`; cada consultório lê só a própria.
- **Contas e perfis:** profissional, assistente e admin, por vínculo usuário × consultório. Registro CRM/CRP do profissional, validado pelo operador no `/admin/`.
- **Login:** senha com Argon2, bloqueio progressivo após falhas, 2FA por aplicativo autenticador (TOTP) com segredo cifrado, **obrigatório para profissionais**, sessão que expira por inatividade, lista de dispositivos com encerramento remoto.
- **Plataforma:** planos, consultórios, contratos e pagamentos manuais (via `/admin/`); modo somente leitura para consultório inadimplente.
- **Gestão de usuários** pelo admin do consultório, com limite de profissionais do plano.

## Rodando localmente

Requisitos: Python 3.12+, PostgreSQL 15+.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # gere PSIQ_CHAVE_MESTRA (comando no arquivo)
psql -U postgres -f scripts/db/init-local.sql           # cria o papel psiq_app e o banco
python manage.py migrate
python manage.py criar_consultorio --nome "Minha Clínica" \
    --admin-email voce@exemplo.com --admin-nome "Seu Nome" --admin-senha "uma-senha-longa-123"
python manage.py createsuperuser                         # operador da plataforma (/admin/)
python manage.py runserver
```

O papel do banco **não pode** ser superusuário nem ter `BYPASSRLS`, ou o RLS é ignorado. Há um teste que verifica isso.

## Testes

```bash
pytest
```

Cobrem isolamento entre consultórios (ORM e SQL direto), auditoria append-only, login, bloqueio, 2FA, permissões por perfil e revogação de dispositivos. Precisam de PostgreSQL e do papel `psiq_app` com `CREATEDB`.

## Estrutura

```
config/            configurações Django
apps/core/         RLS, contexto de consultório, middlewares, permissões, cifra de segredos
apps/plataforma/   planos, consultórios, contratos, pagamentos
apps/contas/       usuários, vínculos, profissionais, login, 2FA, dispositivos
apps/auditoria/    registro de auditoria append-only
docs/              especificação
scripts/db/        papéis do banco (desenvolvimento e produção)
```

## Convenções para as próximas etapas

- Tabela de negócio: herdar `ModeloBase`, ter `consultorio` (FK) e chamar `sql_habilitar_rls(tabela)` em uma migração.
- Chaves primárias UUID geradas na aplicação (o RLS não combina com `INSERT ... RETURNING` em tabelas sem política de leitura).
- Filtrar por `request.consultorio` nas consultas, além do RLS (defesa em camadas).
- Nunca gravar conteúdo clínico em logs, auditoria ou mensagens.
- Migração de dados em tabela com RLS precisa definir o contexto (`apps.core.tenancy.contexto`).

## Pendências conhecidas da Etapa 1

- Códigos de recuperação do 2FA e redefinição de senha por e-mail (dependem da camada de avisos, Etapa 2).
- Expiração mais curta (15 min) nas áreas de prontuário (Etapa 3). Hoje a inatividade vale para todo o sistema (`PSIQ_INATIVIDADE_SEGUNDOS`).
- Reuso do mesmo código TOTP dentro da janela de validade não é bloqueado.
- 2FA obrigatório para o operador no `/admin/` (Etapa 6).
- Tailwind e HTMX entram junto com as telas das próximas etapas; a Etapa 1 usa CSS simples em `static/css/psiq.css`.

## Etapa 2: pacientes, agenda e avisos

- **Pacientes** (`apps/pacientes`): CPF único por consultório (aceita máscara), responsáveis legais, pagador do recibo, casal/família/grupo, mesclagem de duplicados que reaponta todo o histórico e arquiva o duplicado (nada é apagado, tudo auditado). O profissional enxerga só os seus pacientes; assistente e admin, todos do consultório.
- **Agenda** (`apps/agenda`): consultas com detecção de conflito por profissional, recorrência semanal ou quinzenal (cada sessão é uma consulta; cancelar uma não apaga a série), remarcação, confirmação, realizada/falta, cancelamento com prazo configurável por consultório (padrão 24 h) e marca de cancelamento tardio, link Jitsi único por consulta online, solicitações de horário com aprovação (a primeira consulta sempre passa por aprovação; retornos, só se o paciente estiver marcado), horários livres a partir das regras semanais.
- **Avisos** (`apps/agenda/avisos.py`): canais plugáveis (hoje e-mail), texto neutro que não cita tipo de atendimento, registro de cada envio e de falhas. O link do aviso abre uma página sem login que só confirma ou cancela aquela consulta (token assinado; a ação é POST, para que leitores de e-mail não confirmem sozinhos).
- **Lembretes:** `python manage.py enviar_lembretes` envia o lembrete das consultas das próximas 24 h, uma vez por consulta. Agende a cada 15 minutos (cron).
- **Painel:** consultas do dia, solicitações pendentes e pacientes sem consulta há mais de 60 dias.

Pendente da Etapa 2: aviso por push (PWA), mensagem ao recusar solicitação, FullCalendar e HTMX na agenda, fila assíncrona (hoje os avisos saem na hora, dentro da requisição), regras semanais de atendimento sem tela (cadastradas pelo `/admin/`) e aviso único por série (hoje só a primeira sessão recebe confirmação).
