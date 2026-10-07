# PSIQ

Plataforma SaaS de gestão de agenda, pacientes, financeiro e prontuário para psicólogos e psiquiatras. Vários consultórios independentes, dados isolados por consultório.

A especificação fica na pasta `docs/`, mantida só localmente (fora do Git).

## Estado

| Etapa | Conteúdo | Situação |
|---|---|---|
| 1 | Base: consultórios, usuários, perfis, login + 2FA, dispositivos, auditoria, RLS | Pronta, testes verdes no CI |
| 2 | Pacientes e agenda, com avisos por e-mail | Pronta, testes verdes no CI (push, FullCalendar e HTMX pendentes) |
| 3 | Prontuário e documentos | Pronta, testes verdes no CI |
| 4 | Financeiro, convênio e repasse | Pronta, testes verdes no CI |
| 5 | Portal do paciente, teleconsulta, calendários | Pronta, testes verdes no CI |
| 6 | Painel interno, cobrança, relatórios, importação | Pronta, testes verdes no CI |

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

## Etapa 3: prontuário e documentos

- **Criptografia por campo** (`apps/prontuario/chaves.py`, `apps/core/cripto.py`): cada profissional tem uma chave de dados, guardada no banco cifrada pela chave mestra (que fica fora do banco e do backup). Conteúdo, CID e arquivos são cifrados com ela; cada versão registra a chave que usou, então a rotação (`rotacionar_chaves`) não quebra o histórico. `PSIQ_CHAVE_MESTRA` aceita várias chaves separadas por vírgula; `rotacionar_chave_mestra` reescreve os segredos com a primeira.
- **Versões append-only:** editar cria uma versão nova; o banco bloqueia `UPDATE` de versões e só permite `DELETE` na exclusão antecipada autorizada (flag local à transação, fechado ao fim da operação).
- **Quem lê:** só o profissional dono. Assistente e admin leem apenas se o dono liberar. Outro profissional precisa, além da liberação, do aceite eletrônico do paciente (link por e-mail com data, hora, IP e versão do termo, revogável pelo mesmo link; revogar derruba o acesso). Toda abertura e leitura é auditada. Exige 2FA, não fica em cache do navegador e a sessão na área de prontuário expira em 15 min de inatividade (`PSIQ_INATIVIDADE_PRONTUARIO_SEGUNDOS`). Quem não pode ler recebe 404.
- **Anexos:** extensões permitidas, limite de 10 MB, cifrados antes de ir ao armazenamento, com hash de integridade. Hoje o armazenamento é em disco (`PSIQ_ANEXOS_DIR`); a interface (`armazenamento.py`) está pronta para um backend S3-compatível.
- **Documentos:** modelos editáveis por consultório com marcadores (`{{paciente_nome}}` etc.), PDF gerado no servidor. Documentos clínicos ficam no prontuário, cifrados; a declaração de comparecimento usa só dados da agenda e pode ser emitida pela assistente. O profissional decide o que é liberado ao paciente.
- **Retenção:** prazo de guarda calculado (médico 20 anos, psicólogo 5, a partir do último registro). Exclusão antecipada só pelo dono, com nome do paciente digitado, motivo e auditoria; apaga conteúdo e arquivos e mantém um registro mínimo do fato.
- **Delegação:** o admin troca o dono de um prontuário (saída de profissional) sem ver o conteúdo; a troca fica registrada e o novo dono lê as versões antigas.

Pendente da Etapa 3: busca por texto (decisão futura, por causa da cifra), assinatura digital (fora de escopo), pedido de exclusão vindo do portal (Etapa 5) e backend S3 para anexos.

## Etapa 4: financeiro, convênio e repasse

O PSIQ registra e calcula; não movimenta dinheiro, não emite NFS-e e não cobra (docs/01, RF-37).

- **Cobrança automática:** ao marcar uma consulta como realizada, cria o lançamento com o valor da tabela do profissional (particular, ou da operadora se o convênio do paciente casa com um convênio ativo; primeira consulta pode ter valor próprio). Sem valor configurado não inventa cobrança; a tela da consulta oferece "Cobrança desta consulta". Cancelamento tardio só cobra se o consultório optou (`cobra_falta_tardia`).
- **Pagamento e recibo:** forma e data do pagamento; recibo em PDF com numeração sequencial por consultório, em nome do pagador (ou do paciente), com CPF (campos do Receita Saúde), cifrado em repouso; registro do número da nota fiscal emitida fora.
- **Convênio:** fila realizado → faturado → pago ou glosado. Retorno da operadora com valor recebido: total vira pago; parcial vira pago com glosa e motivo obrigatório; zero vira glosado.
- **Repasse:** regras do admin por profissional (percentual ou valor fixo; padrão, particular, convênio ou primeira consulta, a mais específica vence). O repasse nasce quando o dinheiro entra e usa o que de fato entrou (glosa reduz); valor fixo nunca passa do recebido; aluguel de sala não gera repasse. O admin marca como pago; pagamento desfeito remove o repasse a pagar, mas é bloqueado se o repasse já foi pago ou há recibo.
- **Aluguel de sala:** cobrança recorrente mensal; `python manage.py gerar_cobrancas_recorrentes` (pode rodar todo dia, não duplica).
- **Permissões:** profissional vê só os próprios lançamentos e repasses (somente leitura); assistente opera pagamentos, recibos e a fila de convênios; admin tem tudo, incluindo valores, regras, convênios e pagamento de repasse.

Pendente da Etapa 4: relatórios financeiros (Etapa 6), lançamento de consultas em grupo (hoje manual) e vínculo do paciente ao convênio por cadastro (hoje por nome).

## Etapa 5: portal do paciente, teleconsulta, PWA e calendários externos

- **Portal sem senha** (`apps/portal`): o paciente entra em `/portal/<id do consultório>/entrar/` (o link vai nos avisos), recebe um código de 6 dígitos por e-mail (guardado só como hash, vale 10 min, uso único, 5 tentativas, 5 códigos por hora) e a resposta é sempre a mesma, exista o e-mail ou não. Menor de 16 anos só pelo responsável legal; um responsável com vários filhos alterna entre eles.
- **No portal:** próximos compromissos (confirmar, cancelar dentro do prazo, entrar na sala Jitsi 15 min antes), marcar horário (a primeira consulta vira pedido; retorno marca na hora; só horários livres das regras do profissional), pagamentos e recibos, documentos liberados pelo profissional e declarações de comparecimento, e privacidade: quem abriu o prontuário, autorizações de compartilhamento (revogáveis) e pedidos de exclusão, cópia ou correção de dados, que chegam à equipe em "Pedidos de pacientes".
- **PWA:** manifesto e service worker na raiz. O service worker não faz cache nem intercepta requisições (nenhum dado de paciente fica no aparelho); só recebe push.
- **Push (Web Push/VAPID):** canal plugável nos avisos, com o mesmo texto neutro do e-mail; um aviso por aparelho; aparelho que cancelou (404/410) é desativado. Gere as chaves com `npx web-push generate-vapid-keys` e preencha `PSIQ_VAPID_*`.
- **Calendários externos** (`apps/calendarios`): OAuth com Google Calendar e Outlook/Microsoft 365, tokens cifrados pela chave mestra, `state` assinado e amarrado à sessão. Consultas vão ao calendário como "Consulta" e o horário (o nome do paciente só aparece se o profissional ligar essa opção); compromissos pessoais voltam como bloqueios que somem dos horários oferecidos no portal. `python manage.py sincronizar_calendarios` a cada 10 min (cron) ou "Sincronizar agora" na tela. Antes de usar: registrar o app no Google Cloud e no Azure e passar pela revisão deles (pré-requisito do docs/02), e preencher `PSIQ_GOOGLE_*` e `PSIQ_MICROSOFT_*`.

Pendente da Etapa 5: sincronização em tempo real (hoje por cron e sob demanda; a fila assíncrona entra com a infraestrutura), exportação de consultas em grupo para o calendário externo com nomes, e revisão de segurança dedicada (docs/06) antes do primeiro cliente.

## Etapa 6: operação, relatórios, exportação e importação

- **Painel do operador** (`/operador/`, equipe da plataforma com 2FA obrigatório, inclusive no `/admin/`): consultórios com plano, situação, uso (pacientes ativos × limite, profissionais) e cobranças em aberto; cria consultório com o primeiro admin, troca plano e situação, registra cobrança e marca pagamento manual. Só números de conta: nunca conteúdo clínico.
- **Inadimplência** (`atualizar_inadimplencia`, diária): cobrança vencida avisa os admins por e-mail e abre carência de 15 dias; depois o consultório fica em somente leitura. Pagou, volta na hora. Nunca há corte total: leitura e exportação continuam liberadas.
- **Suporte autorizado pelo cliente:** o admin do consultório autoriza um atendente por 1, 4 ou 24 horas, com motivo. O acesso é somente leitura, **nunca alcança prontuários** (a regra de autoria vale para o suporte também), cada requisição vai para a auditoria do cliente e o cliente pode encerrar a qualquer momento.
- **Relatórios** (`/relatorios/`): agenda (faltas, cancelamentos, ocupação), financeiro (faturamento, inadimplência, repasses, glosas por operadora) e pacientes (visão geral, novos, sem consulta há X dias), em tela, CSV (proteção contra injeção de fórmula) e PDF, por perfil (profissional vê só os próprios números; assistente não vê repasses). Nenhum traz conteúdo clínico ou CID; os que listam nomes de pacientes geram auditoria.
- **Exportação completa** (`/exportacao/`, nunca bloqueada): o admin baixa cadastro, agenda, financeiro, usuários, recibos, declarações e auditoria em CSV e JSON (sem prontuários); cada profissional baixa os próprios prontuários decifrados (todas as versões, anexos e documentos), com 2FA e auditoria. Contrato encerrado: 90 dias só para exportar.
- **Importação de pacientes** (`/importacao/`): planilha CSV ou XLSX com modelo para baixar, prévia com validação de CPF, telefone, data e e-mail, aviso de duplicados (CPF já cadastrado ou repetido na planilha; mesmo nome e nascimento vira alerta) e confirmação em um segundo passo. Prontuário antigo entra como anexo "histórico importado".
- **Operação:** `GET /saude/`, `verificar_saude` (backup parado, falhas de aviso, contas com muitas falhas de login, exportações em massa, suporte fora de autorização), `deploy/` com Dockerfile, docker-compose, Caddyfile (TLS) e `OPERACAO.md` (agenda de tarefas, backup, chaves, checklist antes do primeiro cliente).

## O que ainda falta antes do primeiro cliente

Nada disto é código de produto; é o que o próprio roadmap (docs/06) pede:

1. **Revisão jurídica** dos modelos de contrato, política, termo e plano de incidente por advogado.
2. **Infraestrutura:** VPS, backup contínuo (WAL) em outro provedor, restauração testada, homologação separada, provedor de e-mail transacional (SPF/DKIM), chaves VAPID, registro dos apps OAuth no Google e na Microsoft.
3. **Revisão de segurança dedicada** (isolamento, permissões, dados em logs, dependências). Itens conhecidos que ainda não foram feitos: redefinição de senha por e-mail e códigos de recuperação do 2FA, bloqueio de reuso do mesmo código TOTP, limite de taxa (rate limit) nos endpoints públicos (idealmente no proxy), política de CSP (hoje há `onchange` inline em duas telas), fila assíncrona para avisos e sincronizações (hoje na hora ou por cron) e HTMX/FullCalendar/Tailwind (a interface usa CSS próprio e páginas simples).
4. **Piloto** com um consultório real antes de abrir para outros.
