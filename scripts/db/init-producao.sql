-- Producao: papeis separados (docs/02-arquitetura.md, secao 3). Troque as senhas e rode como superusuario.
--   psiq_migrator: dono das tabelas, usado so nas migracoes (DB_USER=psiq_migrator python manage.py migrate)
--   psiq_app:      usado pela aplicacao, sujeito a RLS, sem DDL
--   psiq_backup:   somente leitura, para o backup logico
-- Atencao: o RLS usa FORCE, entao tambem vale para o psiq_migrator. Migracoes de dados precisam definir
-- o contexto (apps.core.tenancy.contexto).
CREATE ROLE psiq_migrator LOGIN PASSWORD 'TROQUE' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE psiq_app LOGIN PASSWORD 'TROQUE' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE psiq_backup LOGIN PASSWORD 'TROQUE' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE psiq OWNER psiq_migrator;

\c psiq
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO psiq_app, psiq_backup;
GRANT CREATE ON SCHEMA public TO psiq_migrator;

ALTER DEFAULT PRIVILEGES FOR ROLE psiq_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO psiq_app;
ALTER DEFAULT PRIVILEGES FOR ROLE psiq_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO psiq_app;
ALTER DEFAULT PRIVILEGES FOR ROLE psiq_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO psiq_backup;
