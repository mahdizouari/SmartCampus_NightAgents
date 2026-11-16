-- =============================================================================
-- SmartCampus AI — PostgreSQL 16 & pgvector Database Initialization (SA-16)
--
-- Initializes:
-- 1. Extensions (uuid-ossp, pgvector)
-- 2. Multi-tenant Schemas (core, iit, isb, isa, knowledge)
-- 3. Least-Privilege Application User (smartcampus_app)
-- 4. RBAC / RLS Security Roles (authenticated_student, human_rh, nau_responsable)
-- =============================================================================

-- Ensure we operate on the target database
\connect smartcampus_db;

-- -----------------------------------------------------------------------------
-- 1. Enable Required Database Extensions
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- -----------------------------------------------------------------------------
-- 2. Create Logical Schemas (SA-4 & SA-14 Architecture)
-- -----------------------------------------------------------------------------
-- 'core': Common accounts, candidates, consent, appointments, outbox, audit
CREATE SCHEMA IF NOT EXISTS core;

-- 'iit', 'isb', 'isa': School-isolated dossiers, evaluations, decisions (RLS protected)
CREATE SCHEMA IF NOT EXISTS iit;
CREATE SCHEMA IF NOT EXISTS isb;
CREATE SCHEMA IF NOT EXISTS isa;

-- 'knowledge': RAG vector chunks (pgvector embeddings) and official cited documents
CREATE SCHEMA IF NOT EXISTS knowledge;

-- -----------------------------------------------------------------------------
-- 3. Create Dedicated Application User with Least Privilege
-- 'smartcampus_app' is the runtime service user for the FastAPI backend.
-- It has NO SUPERUSER rights and only has permissions on the 5 project schemas.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'smartcampus_app') THEN
        CREATE ROLE smartcampus_app WITH LOGIN PASSWORD 'smartcampus_app_secret';
    ELSE
        ALTER ROLE smartcampus_app WITH LOGIN PASSWORD 'smartcampus_app_secret';
    END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 4. Grant Least-Privilege Permissions to 'smartcampus_app'
-- -----------------------------------------------------------------------------
-- Allow connection to the dedicated database
GRANT CONNECT ON DATABASE smartcampus_db TO smartcampus_app;

-- Grant USAGE and CREATE on the 5 application schemas
GRANT USAGE, CREATE ON SCHEMA core, iit, isb, isa, knowledge TO smartcampus_app;

-- Grant DML privileges on existing tables and sequences
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, iit, isb, isa, knowledge TO smartcampus_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA core, iit, isb, isa, knowledge TO smartcampus_app;

-- Ensure all future tables and sequences automatically inherit these permissions
ALTER DEFAULT PRIVILEGES IN SCHEMA core, iit, isb, isa, knowledge 
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO smartcampus_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA core, iit, isb, isa, knowledge 
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO smartcampus_app;

-- -----------------------------------------------------------------------------
-- 5. Create Security Roles (For Row-Level Security in SA-5 / SA-11)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'authenticated_student') THEN
        CREATE ROLE authenticated_student NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'human_rh') THEN
        CREATE ROLE human_rh NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'nau_responsable') THEN
        CREATE ROLE nau_responsable NOLOGIN;
    END IF;
END
$$;

-- Grant schema usage to security roles
GRANT USAGE ON SCHEMA core, knowledge TO authenticated_student, human_rh, nau_responsable;
GRANT USAGE ON SCHEMA iit, isb, isa TO human_rh, nau_responsable;
