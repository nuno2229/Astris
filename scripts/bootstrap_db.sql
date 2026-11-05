-- Run ONCE as a Postgres superuser to create the roles and database.
-- psql -v owner_pw=... -v app_pw=... -v dbname=finance_tracker -f scripts/bootstrap_db.sql
-- ft_owner: owns the schema, runs migrations, owns the SECURITY DEFINER functions (BYPASSRLS).
-- ft_app:   runtime role. NOT the owner, NO BYPASSRLS, so row-level security always applies.
SELECT 'CREATE ROLE ft_owner LOGIN BYPASSRLS PASSWORD ' || quote_literal(:'owner_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ft_owner') \gexec
SELECT 'CREATE ROLE ft_app LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD ' || quote_literal(:'app_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ft_app') \gexec
SELECT format('CREATE DATABASE %I OWNER ft_owner', :'dbname')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname') \gexec
\connect :dbname
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO ft_owner;
GRANT USAGE ON SCHEMA public TO ft_app;
