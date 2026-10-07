-- Desenvolvimento e testes: um unico papel SEM superusuario e SEM BYPASSRLS.
-- Superusuarios ignoram o RLS, entao os testes de isolamento so valem com este papel.
-- Rode como superusuario:  psql -U postgres -f scripts/db/init-local.sql
CREATE ROLE psiq_app LOGIN PASSWORD 'psiq_dev' NOSUPERUSER NOBYPASSRLS CREATEDB;
CREATE DATABASE psiq OWNER psiq_app;
