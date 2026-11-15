# SmartCampus AI — Conception des Tables de Gestion des Documents et de Synchronisation Vectorielle (`SA-14`)

> **Tâche :** `[SA-14]` Concevoir les tables de gestion des documents et de synchronisation (Issue GitHub `#18` · Story `SA-11: Modèle Relationnel`)  
> **Dépendances :** `SA-3` (Acteurs & Workflows), `SA-4` (Architecture globale 5 couches), `SA-5` (Contrat REST & RBAC)  
> **Règle d'or :** *"Zero invented program facts: all knowledge served from cited pgvector chunks."*

---

## 1. Contexte, Objectifs & Cycle de Vie RAG

Dans l'écosystème SmartCampus AI, le sous-agent conversationnel **`VISION 1.2 Librarian`** s'interdit formellement de générer des frais de scolarité, des dates limites, des seuils d'accès ou des contenus de formation à partir des poids probabilistes de son modèle. Chaque affirmation doit être sourcée à partir du corpus documentaire officiel indexé.

Ce corpus académique (brochures des filières, règlements d'admission, grilles des frais, catalogues des cours de l'**IIT**, de l'**ISB** et de l'extension **ISA**) est administré par le `NAU Responsable` et le `Platform Admin`.

### Objectifs d'Ingénierie des Données (`SA-14`) :
1. **Enregistrement et Métadonnées :** Référencer chaque document administratif officiel avec ses attributs métier (école, filière ciblée, cycle, validité).
2. **Gestion du Versioning Immuable :** Tracer l'historique complet des versions publiées, le fichier physique dans MinIO/S3, son empreinte cryptographique (hash SHA-256) et les notes de version.
3. **Suivi des Traitements Automatiques Asynchrones :** Piloter l'état d'avancement des pipelines d'ingestion (extraction textuelle, découpage en fragments/chunks, génération des embeddings vectoriels, indexation).
4. **Synchronisation Réactive de la Base Vectorielle (`pgvector`) :**
   * **Ajout :** Ingestion et activation des nouveaux vecteurs dans le schéma `knowledge`.
   * **Mise à jour (Nouvelle Version) :** Ingestion de la version $N+1$, bascule atomique du pointeur actif, et archivage/désactivation des vecteurs de la version $N$.
   * **Suppression :** Retrait immédiat des vecteurs de la recherche sémantique (soft delete ou purge) et invalidation des caches chauds Redis.

---

## 2. Relations & Organisation du Schéma

Le modèle articule 5 tables principales réparties entre la gestion administrative (`core`) et l'indexation sémantique (`knowledge`) :

* **`core.documents` (1)** $\longrightarrow$ **`core.document_versions` ($N$) :** Un document administratif possède un historique de plusieurs versions successives, dont une seule est marquée active.
* **`core.document_versions` (1)** $\longrightarrow$ **`core.document_sync_jobs` ($N$) :** Chaque version téléversée déclenche un ou plusieurs travaux asynchrones d'ingestion ou de réindexation.
* **`core.document_sync_jobs` (1)** $\longrightarrow$ **`core.document_sync_logs` ($N$) :** Chaque travail produit une trace chronologique détaillée pour le diagnostic des pipelines.
* **`core.document_versions` (1)** $\longrightarrow$ **`knowledge.chunks` ($N$) :** Une version active est découpée en fragments textuels avec embeddings denses stockés dans `pgvector`.

---

## 3. Définition Détaillée des Tables SQL

Les tables de gestion documentaire résident dans le schéma relationnel **`core`**, tandis que les fragments vectoriels et leur index de similarité sont isolés dans le schéma **`knowledge`**.

### 3.1 Table `core.documents` (Catalogue des Documents Officiels)
Enregistre la référence maîtresse d'un document académique, son périmètre d'application au sein de NAU et son état courant.

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique du document |
| `code` | `VARCHAR(64)` | `UNIQUE NOT NULL` | Code de nomenclature métier (ex: `IIT-ING-GL-2026`, `ISB-FEES-2026`) |
| `title` | `VARCHAR(255)` | `NOT NULL` | Titre officiel complet du document |
| `description` | `TEXT` | `NULL` | Description du contenu et public cible |
| `school` | `VARCHAR(20)` | `NOT NULL CHECK (school IN ('iit', 'isb', 'isa', 'nau_common'))` | Établissement de rattachement ou transversal |
| `category` | `VARCHAR(30)` | `NOT NULL CHECK (category IN ('admission_rules', 'curriculum', 'fees_schedule', 'equivalence_grid', 'general_brochure'))` | Catégorie fonctionnelle pour filtrage RAG |
| `target_program_id` | `VARCHAR(64)` | `NULL` | Référence optionnelle vers une filière précise (`iit.programs` / `isb.programs`) |
| `current_version_id` | `UUID` | `NULL` | Référence vers la version active (`core.document_versions.id`) |
| `status` | `VARCHAR(20)` | `NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'DELETED'))` | Statut du cycle de vie documentaire |
| `created_by` | `UUID` | `NOT NULL REFERENCES core.accounts(id)` | Responsable ou administrateur créateur |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | Date de création |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | Dernière mise à jour |

---

### 3.2 Table `core.document_versions` (Gestion des Versions & Stockage MinIO)
Stocke chaque version physique téléversée de façon immuable. Un document peut avoir $N$ versions successives, mais une seule version est active à la fois.

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique de la version |
| `document_id` | `UUID` | `NOT NULL REFERENCES core.documents(id) ON DELETE CASCADE` | Lien vers le document parent |
| `version_number` | `INTEGER` | `NOT NULL CHECK (version_number > 0)` | Numéro incrémental de version (`1`, `2`, `3`...) |
| `file_name` | `VARCHAR(255)` | `NOT NULL` | Nom d'origine du fichier (ex: `Guide_IIT_2026_v2.pdf`) |
| `file_path_storage` | `VARCHAR(512)` | `NOT NULL` | Chemin de stockage objet MinIO/S3 (chiffré au repos) |
| `mime_type` | `VARCHAR(100)` | `NOT NULL CHECK (mime_type IN ('application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/markdown', 'text/plain'))` | Format MIME validé |
| `file_size_bytes` | `BIGINT` | `NOT NULL CHECK (file_size_bytes > 0)` | Taille du fichier en octets |
| `file_hash_sha256` | `CHAR(64)` | `NOT NULL` | Empreinte cryptographique pour détecter les doublons stricts |
| `uploaded_by` | `UUID` | `NOT NULL REFERENCES core.accounts(id)` | Responsable ayant importé cette version |
| `changelog` | `TEXT` | `NULL` | Résumé des modifications apportées dans cette version |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT false` | `true` si cette version est celle actuellement requêtée par le RAG |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | Date d'importation |

* **Contrainte d'unicité composite :** `UNIQUE (document_id, version_number)` — Interdit les doublons de version pour un même document.
* **Contrainte d'intégrité :** `UNIQUE (file_hash_sha256, document_id)` — Évite le ré-upload inutile d'un fichier identique.

---

### 3.3 Table `core.document_sync_jobs` (Suivi du Pipeline Automatique d'Ingestion)
Suit l'exécution asynchrone des tâches en arrière-plan (orchestrées par Celery / FastAPI Background Tasks) chargées de transformer le document brut en vecteurs exploitables.

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique du travail de synchronisation |
| `version_id` | `UUID` | `NOT NULL REFERENCES core.document_versions(id) ON DELETE CASCADE` | Version soumise au traitement |
| `action_type` | `VARCHAR(20)` | `NOT NULL CHECK (action_type IN ('INGEST', 'REINDEX', 'DEACTIVATE', 'PURGE'))` | Nature de l'opération demandée |
| `pipeline_stage` | `VARCHAR(20)` | `NOT NULL DEFAULT 'PARSING' CHECK (pipeline_stage IN ('PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING', 'COMPLETED', 'FAILED'))` | Étape active du pipeline |
| `status` | `VARCHAR(20)` | `NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSING', 'SYNCED', 'FAILED', 'CANCELLED'))` | Statut global du job |
| `embedding_model` | `VARCHAR(100)` | `NOT NULL DEFAULT 'text-embedding-3-small'` | Modèle d'embedding utilisé |
| `chunk_size` | `INTEGER` | `NOT NULL DEFAULT 512` | Taille nominale des fragments (tokens ou caractères) |
| `chunk_overlap` | `INTEGER` | `NOT NULL DEFAULT 64` | Chevauchement entre fragments consécutifs |
| `total_pages` | `INTEGER` | `DEFAULT 0` | Nombre total de pages extraites |
| `total_chunks` | `INTEGER` | `DEFAULT 0` | Nombre total de fragments générés |
| `total_tokens` | `INTEGER` | `DEFAULT 0` | Volume total de tokens vectorisés |
| `error_message` | `TEXT` | `NULL` | Message d'erreur explicite en cas d'échec |
| `error_traceback` | `TEXT` | `NULL` | Trace technique pour debugging |
| `retry_count` | `INTEGER` | `NOT NULL DEFAULT 0` | Nombre de tentatives de réexécution |
| `max_retries` | `INTEGER` | `NOT NULL DEFAULT 3` | Plafond avant passage en échec permanent |
| `started_at` | `TIMESTAMPTZ` | `NULL` | Heure exacte de début d'exécution |
| `completed_at` | `TIMESTAMPTZ` | `NULL` | Heure exacte d'achèvement |
| `duration_ms` | `INTEGER` | `NULL` | Durée totale d'exécution en millisecondes |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | Date d'émission de l'ordre de synchronisation |

---

### 3.4 Table `core.document_sync_logs` (Journal d'Audit & Diagnostic du Pipeline)
Fournit une traçabilité granulaire de chaque phase du traitement automatique pour les équipes de supervision et le support.

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `BIGSERIAL` | `PRIMARY KEY` | Identifiant séquentiel de log |
| `job_id` | `UUID` | `NOT NULL REFERENCES core.document_sync_jobs(id) ON DELETE CASCADE` | Job parent |
| `stage` | `VARCHAR(20)` | `NOT NULL` | Phase concernée (`EXTRACTOR`, `TOKENIZER`, `EMBEDDER`, `PGVECTOR_WRITER`) |
| `level` | `VARCHAR(10)` | `NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR'))` | Gravité du log |
| `message` | `TEXT` | `NOT NULL` | Description de l'événement |
| `details` | `JSONB` | `DEFAULT '{}'::jsonb` | Métriques intermédiaires (ex: temps par lot, tokens consommés) |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | Horodatage du log |

---

### 3.5 Table `knowledge.chunks` (Base Vectorielle `pgvector`)
Contient les segments de texte indexés par recherche vectorielle et recherche hybride (BM25 + cosine similarity).

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique du chunk vectorisé |
| `document_id` | `UUID` | `NOT NULL REFERENCES core.documents(id) ON DELETE CASCADE` | Lien vers le document source |
| `version_id` | `UUID` | `NOT NULL REFERENCES core.document_versions(id) ON DELETE CASCADE` | Lien vers la version exacte dont il est issu |
| `chunk_index` | `INTEGER` | `NOT NULL CHECK (chunk_index >= 0)` | Ordre séquentiel du chunk dans le document |
| `content` | `TEXT` | `NOT NULL` | Contenu textuel brut indexé |
| `token_count` | `INTEGER` | `NOT NULL CHECK (token_count > 0)` | Nombre de tokens calculé |
| `embedding` | `vector(1536)` | `NOT NULL` | Vecteur dense (dimension 1536 standard OpenAI/BGE) |
| `metadata` | `JSONB` | `NOT NULL DEFAULT '{}'::jsonb` | Métadonnées de citation (`school`, `program`, `page_number`, `source_title`, `citation_tag`) |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT true` | Flag de visibilité immédiate pour la recherche sémantique |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | Date d'indexation |

* **Index HNSW :** `CREATE INDEX idx_chunks_embedding_hnsw ON knowledge.chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);`
* **Index de Filtrage Rapide :** `CREATE INDEX idx_chunks_active_doc ON knowledge.chunks (is_active, document_id);`
* **Index JSONB GIN :** `CREATE INDEX idx_chunks_metadata_gin ON knowledge.chunks USING gin (metadata);`

---

## 4. Script DDL PostgreSQL Complet (`V3__create_document_sync_tables.sql`)

Ce script prêt pour migration Alembic / Flyway implémente l'ensemble des tables, contraintes et index :

```sql
-- =============================================================================
-- SMARTCAMPUS AI — SCRIPT DDL : GESTION DES DOCUMENTS & SYNCHRONISATION (SA-14)
-- Schémas cibles : core (relationnel) & knowledge (pgvector)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector" SCHEMA knowledge;

-- -----------------------------------------------------------------------------
-- 1. Table Maîtresse des Documents
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(64) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    school VARCHAR(20) NOT NULL CHECK (school IN ('iit', 'isb', 'isa', 'nau_common')),
    category VARCHAR(30) NOT NULL CHECK (category IN (
        'admission_rules', 'curriculum', 'fees_schedule', 'equivalence_grid', 'general_brochure'
    )),
    target_program_id VARCHAR(64),
    current_version_id UUID, -- Clé étrangère ajoutée après création de document_versions
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'DELETED')),
    created_by UUID NOT NULL REFERENCES core.accounts(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_documents_school_category ON core.documents(school, category);
CREATE INDEX idx_documents_status ON core.documents(status);

-- -----------------------------------------------------------------------------
-- 2. Table des Versions Physiques des Documents
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.document_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES core.documents(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    file_name VARCHAR(255) NOT NULL,
    file_path_storage VARCHAR(512) NOT NULL,
    mime_type VARCHAR(100) NOT NULL CHECK (mime_type IN (
        'application/pdf', 
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 
        'text/markdown', 
        'text/plain'
    )),
    file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes > 0),
    file_hash_sha256 CHAR(64) NOT NULL,
    uploaded_by UUID NOT NULL REFERENCES core.accounts(id),
    changelog TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_document_version_number UNIQUE (document_id, version_number),
    CONSTRAINT uq_document_file_hash UNIQUE (document_id, file_hash_sha256)
);

CREATE INDEX idx_document_versions_active ON core.document_versions(document_id, is_active);

-- Ajout de la contrainte FK circulaire sécurisée sur current_version_id
ALTER TABLE core.documents 
    ADD CONSTRAINT fk_documents_current_version 
    FOREIGN KEY (current_version_id) REFERENCES core.document_versions(id) ON DELETE SET NULL;

-- -----------------------------------------------------------------------------
-- 3. Table de Suivi des Traitements Automatiques (Jobs Asynchrones)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.document_sync_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES core.document_versions(id) ON DELETE CASCADE,
    action_type VARCHAR(20) NOT NULL CHECK (action_type IN ('INGEST', 'REINDEX', 'DEACTIVATE', 'PURGE')),
    pipeline_stage VARCHAR(20) NOT NULL DEFAULT 'PARSING' CHECK (pipeline_stage IN (
        'PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING', 'COMPLETED', 'FAILED'
    )),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN (
        'PENDING', 'PROCESSING', 'SYNCED', 'FAILED', 'CANCELLED'
    )),
    embedding_model VARCHAR(100) NOT NULL DEFAULT 'text-embedding-3-small',
    chunk_size INTEGER NOT NULL DEFAULT 512,
    chunk_overlap INTEGER NOT NULL DEFAULT 64,
    total_pages INTEGER DEFAULT 0,
    total_chunks INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    error_message TEXT,
    error_traceback TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sync_jobs_status ON core.document_sync_jobs(status);
CREATE INDEX idx_sync_jobs_version ON core.document_sync_jobs(version_id);

-- -----------------------------------------------------------------------------
-- 4. Table des Journaux Granulaires d'Exécution (Logs de Diagnostic)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.document_sync_logs (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES core.document_sync_jobs(id) ON DELETE CASCADE,
    stage VARCHAR(30) NOT NULL,
    level VARCHAR(10) NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
    message TEXT NOT NULL,
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sync_logs_job ON core.document_sync_logs(job_id, created_at);

-- -----------------------------------------------------------------------------
-- 5. Table Vectorielle de Connaissances (pgvector)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge.chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES core.documents(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES core.document_versions(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count > 0),
    embedding knowledge.vector(1536) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index vectoriel HNSW pour recherche sémantique ultra-rapide par cosinus
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw 
    ON knowledge.chunks 
    USING hnsw (embedding knowledge.vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Index de filtrage pour recherche hybride et exclusion des chunks obsolètes
CREATE INDEX idx_knowledge_chunks_active_doc 
    ON knowledge.chunks (is_active, document_id);

CREATE INDEX idx_knowledge_chunks_metadata_gin 
    ON knowledge.chunks USING gin (metadata);
```

---

## 5. Machine à États du Traitement Automatique & Synchronisation

Le cycle de vie du traitement asynchrone est strictement contrôlé par les transitions suivantes :

```text
[ Document Uploadé ]
         |
         v
     (PENDING) --------------> [ Worker Celery déclenché ]
         |
         v
    (PROCESSING)
         |
         +---> Stage: PARSING   (Extraction texte via PyPDF / docx2txt)
         |
         +---> Stage: CHUNKING  (Découpage récursif avec overlap 64)
         |
         +---> Stage: EMBEDDING (Appel API vectorisation par batchs)
         |
         +---> Stage: INDEXING  (Écriture transactionnelle dans knowledge.chunks)
         |
         v
     [ Succès ? ]
      /        \
    OUI        NON
    /            \
   v              v
(SYNCED)       [ retry_count < max_retries ? ]
                  /          \
                OUI          NON
                /              \
               v                v
          (PENDING)          (FAILED)
       (Retry après backoff) (Ticket d'alerte admin)
```

---

## 6. Procédures de Synchronisation Réactive

### 6.1 Cas 1 : Ajout d'un Nouveau Document
1. L'administrateur téléverse le fichier dans MinIO (`POST /api/v1/documents/upload`).
2. Une transaction SQL crée l'enregistrement dans `core.documents` et la version initiale `1` dans `core.document_versions` avec `is_active = FALSE`.
3. Un événement est injecté dans `core.document_sync_jobs` avec l'action `INGEST`.
4. Le worker exécute le pipeline. En fin d'indexation réussie :
   * Les chunks sont insérés dans `knowledge.chunks` avec `is_active = TRUE`.
   * La version `1` passe à `is_active = TRUE`.
   * `core.documents.current_version_id` est mis à jour vers cette version.
   * `core.documents.status` passe à `'PUBLISHED'`.
   * Le job passe à `'SYNCED'`.

### 6.2 Cas 2 : Modification / Mise à Jour (Nouvelle Version)
Lorsqu'un responsable importe un document révisé (ex: nouvelle grille des frais 2026/2027) :
1. Une nouvelle version `N+1` est insérée dans `core.document_versions` (`is_active = FALSE`).
2. Le job de synchronisation extrait et indexe les nouveaux fragments dans `knowledge.chunks` marqués temporairement sous `version_id = N+1`.
3. **Bascule Atomique (Single Transaction) :**
   ```sql
   BEGIN;
     -- 1. Désactiver les anciens chunks de la version N
     UPDATE knowledge.chunks 
     SET is_active = FALSE 
     WHERE document_id = :doc_id AND version_id = :old_version_id;

     -- 2. Désactiver l'ancienne version
     UPDATE core.document_versions 
     SET is_active = FALSE 
     WHERE id = :old_version_id;

     -- 3. Activer la nouvelle version N+1
     UPDATE core.document_versions 
     SET is_active = TRUE 
     WHERE id = :new_version_id;

     -- 4. Pointer le document vers sa nouvelle version
     UPDATE core.documents 
     SET current_version_id = :new_version_id, updated_at = CURRENT_TIMESTAMP 
     WHERE id = :doc_id;

     -- 5. Activer les nouveaux chunks
     UPDATE knowledge.chunks 
     SET is_active = TRUE 
     WHERE document_id = :doc_id AND version_id = :new_version_id;
   COMMIT;
   ```
4. Le cache conversationnel RAG dans Redis est invalidé pour forcer `VISION 1.2 Librarian` à requêter immédiatement les nouveaux chunks.

### 6.3 Cas 3 : Suppression d'un Document
1. L'administrateur demande la suppression d'un document (`DELETE /api/v1/documents/{id}`).
2. **Soft Delete immédiat :** `core.documents.status = 'DELETED'`.
3. **Désactivation instantanée :**
   ```sql
   UPDATE knowledge.chunks SET is_active = FALSE WHERE document_id = :doc_id;
   UPDATE core.document_versions SET is_active = FALSE WHERE document_id = :doc_id;
   ```
4. Les fragments n'apparaissent plus dans aucun résultat de recherche sémantique du RAG.
5. Une tâche différée de purge (`action_type = 'PURGE'`) peut ultérieurement supprimer physiquement les fichiers MinIO et les vecteurs après la fin de la campagne académique.

---

## 7. Règles d'Accès RBAC Associées aux Tables Documentaires

| Rôle | `core.documents` | `core.document_versions` | `core.document_sync_jobs` | `knowledge.chunks` |
| :--- | :---: | :---: | :---: | :---: |
| **`ANONYMOUS_GUEST`** | — | — | — | **Lecture vectorielle RAG** (`is_active = TRUE`) |
| **`AUTHENTICATED_STUDENT`** | — | — | — | **Lecture vectorielle RAG** (`is_active = TRUE`) |
| **`HUMAN_RH`** | Lecture seule | Lecture seule | Lecture logs | Lecture vectorielle RAG |
| **`NAU_RESPONSABLE`** | **CRUD (École assignée)** | **Création / Versioning** | **Consultation statuts** | Lecture vectorielle RAG |
| **`PLATFORM_ADMIN`** | **CRUD Total** | **CRUD Total** | **Relance manuelle / Retry** | Maintenance & Réindexation |
