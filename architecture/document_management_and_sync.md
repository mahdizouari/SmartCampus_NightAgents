# SmartCampus AI — Conception des Tables de Gestion des Documents et de Synchronisation Vectorielle (`SA-14` / `SA-49` / `SA-56`)

> **Tâche :** `[SA-14]` Concevoir les tables de gestion des documents et de synchronisation (étendu par `SA-49`, `SA-50`, `SA-56` et la révision Alembic `20261127_0003`)  
> **Implémentation ORM & API :** `backend/app/models/documents_rag.py`, `backend/app/services/document_sync_service.py`, `backend/app/api/v1/endpoints/knowledge.py`, `backend/scripts/sync_iit_corpus.py`  
> **Règle d'or :** *"Zero invented program facts: all knowledge served from cited pgvector chunks."*

---

## 1. Contexte, Objectifs & Cycle de Vie RAG

Dans l'écosystème SmartCampus AI, le sous-agent conversationnel **`VISION 1.2_Librarian`** s'interdit formellement de générer des frais de scolarité, des dates limites, des seuils d'accès ou des contenus de formation à partir des poids probabilistes de son modèle. Chaque affirmation doit être sourcée à partir du corpus documentaire officiel indexé et des tables PostgreSQL du catalogue.

Ce corpus académique (pages officielles scrapées de `https://iit.tn/`, brochures des filières, règlements d'admission, grilles des frais, catalogues de l'**IIT**, de l'**ISB** et de l'extension **ISA**) est administré directement par le **`Responsable`** (`NAU Responsable`, cloisonné par école `iit | isb | isa` sur `/api/v1/knowledge/documents`). Les rôles historiques `direction` (écriture retirée -> `403 Forbidden`) et `admin` (conservé uniquement pour la compatibilité technique/racine) ne constituent plus des portails métiers distincts.

### Objectifs d'Ingénierie des Données (`SA-14` & `SA-56`)
1. **Enregistrement et Métadonnées (`CorpusDocument` -> `core.documents`) :** Référencer chaque document officiel avec ses attributs métier (école, catégorie documentaire, programme cible optionnel, statut de publication).
2. **Gestion du Versioning Immuable (`CorpusDocumentVersion` -> `core.document_versions`) :** Tracer l'historique complet des versions physiques stockées (`storage/knowledge/<school>/<code>/v<N>/`), l'empreinte cryptographique `SHA-256` et les notes de version.
3. **Suivi des Traitements Asynchrones (`DocumentSyncJob` -> `core.document_sync_jobs` & `DocumentSyncLog` -> `core.document_sync_logs`) :** Piloter l'état des pipelines d'ingestion (`INGEST`, `REINDEX`, `DEACTIVATE`, `PURGE`) avec le modèle d'embedding local open-source **`bge-m3`** (`BAAI/bge-m3`, `1024d`).
4. **Synchronisation Réactive de la Base Vectorielle (`KnowledgeChunk` -> `knowledge.chunks`, `vector(1024)`) :**
   * **Ajout (`NEW_DOCUMENT`) :** Création atomique de `v1` et mise en file d'un job `PENDING INGEST`.
   * **Mise à jour (`NEW_VERSION`) :** Détection `SHA-256`, création de la version `N+1`, bascule atomique du pointeur actif (`activate`) et désactivation des fragments obsolètes.
   * **Idempotence (`UNCHANGED`) :** Zéro version dupliquée et zéro job superflu lorsque le hash `SHA-256` est identique.
   * **Suppression (`DELETED`) :** Désactivation immédiate (`is_active = FALSE`) des versions et vecteurs dans `knowledge.chunks` (ou purge physique différée `PURGE`).

---

## 2. Relations & Correspondance Modèles ORM <-> Tables PostgreSQL

Le modèle unifié dans `backend/app/models/documents_rag.py` articule 5 tables réparties entre les schémas **`core`** et **`knowledge`** *(note : `core.documents` au pluriel désigne le catalogue officiel du corpus RAG `CorpusDocument`, distinct de `core.document` au singulier `CandidateDocument` qui stocke les relevés privés des candidats)* :

* **`core.documents` (`CorpusDocument`, 1)** -> **`core.document_versions` (`CorpusDocumentVersion`, N) :** Historique immuable des versions d'un document officiel, avec `current_version_id` pointant vers l'unique version active.
* **`core.document_versions` (`CorpusDocumentVersion`, 1)** -> **`core.document_sync_jobs` (`DocumentSyncJob`, N) :** Travaux asynchrones d'ingestion, de réindexation ou de purge.
* **`core.document_sync_jobs` (`DocumentSyncJob`, 1)** -> **`core.document_sync_logs` (`DocumentSyncLog`, N) :** Journal chronologique granulaire de diagnostic par étape.
* **`core.document_versions` (`CorpusDocumentVersion`, 1)** -> **`knowledge.chunks` (`KnowledgeChunk`, N) :** Fragments textuels indexés simultanément en dense (`embedding vector(1024)` HNSW cosinus via `BAAI/bge-m3`) et en lexical (`content_tsv TSVECTOR` GIN pour recherche hybride RRF).

---

## 3. Définition Détaillée des Tables SQL (Alignée Migration `20261127_0003`)

### 3.1 Table `core.documents` (Modèle ORM : `CorpusDocument`)

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique du document officiel |
| `code` | `VARCHAR(64)` | `UNIQUE NOT NULL` | Code métier stable (ex: `IIT-WEB-ING-GLID`, `ISB-BROCHURE-2026`) |
| `title` | `VARCHAR(255)` | `NOT NULL` | Titre officiel complet utilisé dans les citations RAG |
| `description` | `TEXT` | `NULL` | Description du contenu ou URL officielle source |
| `school` | `VARCHAR(20)` | `NOT NULL CHECK (school IN ('iit', 'isb', 'isa', 'nau_common'))` | Établissement propriétaire (`school_scope` vérifié pour `responsable`) |
| `category` | `VARCHAR(30)` | `NOT NULL CHECK (category IN ('admission_rules', 'curriculum', 'fees_schedule', 'equivalence_grid', 'general_brochure', 'brochure', 'reglement', 'faq', 'programme'))` | Catégorie fonctionnelle pour le filtrage RAG |
| `target_program_id` | `UUID` | `NULL REFERENCES core.program(id) ON DELETE SET NULL` | Lien optionnel vers une filière du catalogue |
| `current_version_id` | `UUID` | `NULL REFERENCES core.document_versions(id) ON DELETE SET NULL` | Référence vers la version active courante |
| `status` | `VARCHAR(20)` | `NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'DELETED'))` | Statut du cycle de vie documentaire |
| `created_by` | `UUID` | `NOT NULL REFERENCES core.accounts(id) ON DELETE RESTRICT` | Compte `responsable` (ou `admin`) créateur |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Horodatage de création |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Dernière modification |

---

### 3.2 Table `core.document_versions` (Modèle ORM : `CorpusDocumentVersion`)

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique de la version |
| `document_id` | `UUID` | `NOT NULL REFERENCES core.documents(id) ON DELETE CASCADE` | Document parent |
| `version_number` | `INTEGER` | `NOT NULL CHECK (version_number > 0)` | Numéro séquentiel immuable (`1`, `2`, `3`...) |
| `file_name` | `VARCHAR(255)` | `NOT NULL` | Nom de fichier validé (ex: `formation__genie-logiciel.md`) |
| `file_path_storage` | `VARCHAR(512)` | `NOT NULL` | Chemin de stockage objet versionné |
| `mime_type` | `VARCHAR(100)` | `NOT NULL CHECK (mime_type IN ('application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/markdown', 'text/plain'))` | Type MIME vérifié par octets magiques / contenu |
| `file_size_bytes` | `BIGINT` | `NOT NULL CHECK (file_size_bytes > 0)` | Taille exacte en octets |
| `file_hash_sha256` | `CHAR(64)` | `NOT NULL` | Empreinte `SHA-256` pour déduplication stricte (`SA-56`) |
| `uploaded_by` | `UUID` | `NOT NULL REFERENCES core.accounts(id) ON DELETE RESTRICT` | Compte ayant publié cette version |
| `changelog` | `TEXT` | `NULL` | Notes de révision |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` | `TRUE` uniquement pour la version servie au RAG |
| `published_date` | `DATE` | `NULL` | Date officielle de publication |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Date d'enregistrement |

* **Contraintes d'unicité :** `UNIQUE (document_id, version_number)` (`uq_document_version_number`) et `UNIQUE (document_id, file_hash_sha256)` (`uq_document_file_hash`).

---

### 3.3 Table `core.document_sync_jobs` (Modèle ORM : `DocumentSyncJob`)

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique du job de synchronisation |
| `version_id` | `UUID` | `NOT NULL REFERENCES core.document_versions(id) ON DELETE CASCADE` | Version cible |
| `action_type` | `VARCHAR(20)` | `NOT NULL CHECK (action_type IN ('INGEST', 'REINDEX', 'DEACTIVATE', 'PURGE'))` | Opération demandée |
| `pipeline_stage` | `VARCHAR(20)` | `NOT NULL DEFAULT 'PARSING' CHECK (pipeline_stage IN ('PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING', 'COMPLETED', 'FAILED'))` | Étape courante du pipeline |
| `status` | `VARCHAR(20)` | `NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSING', 'SYNCED', 'FAILED', 'CANCELLED'))` | Statut global du job |
| `embedding_model` | `VARCHAR(100)` | `NOT NULL DEFAULT 'bge-m3'` | Modèle d'embedding local open-source (`BAAI/bge-m3`, `1024d`, aligné par `20261127_0003`) |
| `chunk_size` | `INTEGER` | `NOT NULL DEFAULT 512 CHECK (chunk_size > 0)` | Taille nominale des fragments |
| `chunk_overlap` | `INTEGER` | `NOT NULL DEFAULT 64 CHECK (chunk_overlap >= 0)` | Recouvrement inter-fragments |
| `total_pages` | `INTEGER` | `DEFAULT 0` | Pages extraites |
| `total_chunks` | `INTEGER` | `DEFAULT 0` | Fragments générés |
| `total_tokens` | `INTEGER` | `DEFAULT 0` | Volume de tokens indexés |
| `error_message` | `TEXT` | `NULL` | Message d'erreur éventuel |
| `error_traceback` | `TEXT` | `NULL` | Trace technique de diagnostic |
| `retry_count` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK (retry_count >= 0)` | Tentatives effectuées |
| `max_retries` | `INTEGER` | `NOT NULL DEFAULT 3 CHECK (max_retries > 0)` | Plafond de réessais |
| `started_at` | `TIMESTAMPTZ` | `NULL` | Début d'exécution |
| `completed_at` | `TIMESTAMPTZ` | `NULL` | Fin d'exécution |
| `duration_ms` | `INTEGER` | `NULL` | Durée totale en millisecondes |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Date de mise en file |

---

### 3.4 Table `core.document_sync_logs` (Modèle ORM : `DocumentSyncLog`)

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `BIGINT` | `GENERATED ALWAYS AS IDENTITY PRIMARY KEY` | Identifiant séquentiel |
| `job_id` | `UUID` | `NOT NULL REFERENCES core.document_sync_jobs(id) ON DELETE CASCADE` | Job parent |
| `stage` | `VARCHAR(30)` | `NOT NULL` | Composant émetteur (`CHANGE_DETECTOR`, `EXTRACTOR`, `CHUNKER`, `EMBEDDER`, `PGVECTOR_WRITER`) |
| `level` | `VARCHAR(10)` | `NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR'))` | Niveau de sévérité |
| `message` | `TEXT` | `NOT NULL` | Message de diagnostic |
| `details` | `JSONB` | `DEFAULT '{}'::jsonb` | Détails structurés (`change_type`, `file_hash_sha256`, compteurs) |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Horodatage |

---

### 3.5 Table `knowledge.chunks` (Modèle ORM : `KnowledgeChunk` — `pgvector` `1024d` + `TSVECTOR`)

| Colonne | Type | Contraintes | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Identifiant unique du fragment |
| `document_id` | `UUID` | `NOT NULL REFERENCES core.documents(id) ON DELETE CASCADE` | Document officiel parent |
| `version_id` | `UUID` | `NOT NULL REFERENCES core.document_versions(id) ON DELETE CASCADE` | Version exacte source (`UNIQUE (version_id, chunk_index)`) |
| `program_id` | `UUID` | `NULL REFERENCES core.program(id) ON DELETE SET NULL` | Filière cible optionnelle |
| `chunk_index` | `INTEGER` | `NOT NULL CHECK (chunk_index >= 0)` | Position séquentielle dans la version |
| `content` | `TEXT` | `NOT NULL` | Passage officiel servant de preuve citée |
| `section_ref` | `VARCHAR(100)` | `NULL` | Référence de section ou de page |
| `token_count` | `INTEGER` | `NOT NULL CHECK (token_count > 0)` | Nombre de tokens du passage |
| `content_tsv` | `TSVECTOR` | `GENERATED ALWAYS AS (to_tsvector('french', content)) STORED NOT NULL` | Vecteur plein-texte persisté pour la fusion hybride RRF |
| `embedding` | `vector(1024)` | `NOT NULL` | Embedding dense multilingue **`BAAI/bge-m3` (`1024d`)** (migration `20261127_0003`) |
| `metadata` | `JSONB` | `NOT NULL DEFAULT '{}'::jsonb` | Métadonnées (`school`, `program`, `source_url`, `source_title`, `citation_tag`) |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT TRUE` | Visibilité immédiate pour `VISION 1.2_Librarian` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()` | Date d'indexation |

* **Index HNSW cosinus (`1024d`) :** `CREATE INDEX idx_knowledge_chunks_embedding_hnsw ON knowledge.chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);`
* **Index lexical GIN :** `CREATE INDEX idx_knowledge_chunks_tsv ON knowledge.chunks USING gin (content_tsv);`
* **Index de filtrage actif & métadonnées :** `idx_knowledge_chunks_active_doc (is_active, document_id)`, `idx_knowledge_chunks_program (program_id) WHERE program_id IS NOT NULL`, `idx_knowledge_chunks_metadata_gin USING gin (metadata)`.

---

## 4. Extrait DDL PostgreSQL 16 Aligné (`0001` + `20261127_0003`)

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- Alignement Alembic 20261127_0003 : BAAI/bge-m3 (1024 dimensions)
ALTER TABLE core.document_sync_jobs
    ALTER COLUMN embedding_model SET DEFAULT 'bge-m3';

CREATE TABLE IF NOT EXISTS knowledge.chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES core.documents(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES core.document_versions(id) ON DELETE CASCADE,
    program_id UUID REFERENCES core.program(id) ON DELETE SET NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL,
    section_ref VARCHAR(100),
    token_count INTEGER NOT NULL CHECK (token_count > 0),
    content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('french', content)) STORED NOT NULL,
    embedding vector(1024) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_chunks_version_idx UNIQUE (version_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw
    ON knowledge.chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tsv
    ON knowledge.chunks USING gin (content_tsv);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_active_doc
    ON knowledge.chunks (is_active, document_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_metadata_gin
    ON knowledge.chunks USING gin (metadata);
```

---

## 5. Machine à États du Pipeline d'Ingestion & de Synchronisation

```text
[ Document officiel créé ou versionné (/api/v1/knowledge/documents) ]
         |
         v
     (PENDING) --------------> [ Worker asynchrone déclenché ]
         |
         v
    (PROCESSING)
         |
         +---> Stage: PARSING   (Extraction texte Markdown / PDF / DOCX)
         |
         +---> Stage: CHUNKING  (Découpage structuré 512 tokens, overlap 64)
         |
         +---> Stage: EMBEDDING (Vectorisation locale BAAI/bge-m3 1024d sur Ollama sans clé)
         |
         +---> Stage: INDEXING  (Écriture transactionnelle dans knowledge.chunks + TSVECTOR)
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
       (Retry backoff)    (Diagnostic dans core.document_sync_logs)
```

---

## 6. Procédures de Synchronisation Réactive & Corpus Officiel `https://iit.tn/`

### 6.1 Cas 1 : Ajout d'un Nouveau Document (`POST /api/v1/knowledge/documents`)
1. Le **`Responsable`** authentifié (cloisonné sur son `school_scope`, ex. `iit`) téléverse un fichier PDF/DOCX/MD/TXT ou soumet un contenu Markdown structuré sur `POST /api/v1/knowledge/documents`.
2. Une transaction unique enregistre `core.documents` (`CorpusDocument`), sauvegarde la version `v1` avec son empreinte `SHA-256` dans `core.document_versions`, crée un job `PENDING INGEST` (`embedding_model = 'bge-m3'`) dans `core.document_sync_jobs`, et journalise l'opération dans `core.document_sync_logs` et `core.audit_log`.

### 6.2 Cas 2 : Détection de Changements & Nouvelle Version (`SA-56`)
1. **Simulation sans effet de bord (`POST /api/v1/knowledge/documents/check-changes`) :** Calcule le `SHA-256` et classifie déterministement l'entrée (`NEW_DOCUMENT`, `NEW_VERSION`, `METADATA_CHANGED`, `UNCHANGED`, `DUPLICATE_HISTORICAL_VERSION`).
2. **Téléversement versionné (`POST /api/v1/knowledge/documents/{id}/versions`) :** Si le `SHA-256` est identique (`UNCHANGED`), retourne `200 OK` sans créer de doublon. Si le contenu a évolué (`NEW_VERSION`), crée la version immuable `v{N+1}` (`201 Created`) et planifie un job `REINDEX`.
3. **Promotion atomique ou rollback (`POST /api/v1/knowledge/documents/{id}/versions/{version_number}/activate`) :** Désactive dans une transaction SQL unique l'ancienne version et ses `knowledge.chunks`, active la version cible et met à jour `core.documents.current_version_id`.

### 6.3 Cas 3 : Suppression Sécurisée (`DELETE /api/v1/knowledge/documents/{id}`)
1. Passe immédiatement `core.documents.status = 'DELETED'` et désactive (`is_active = FALSE`) toutes les versions et tous les fragments associés dans `knowledge.chunks`, tout en planifiant un job `DEACTIVATE` (ou `PURGE` si `?purge_storage=true`).

### 6.4 Corpus Web Officiel `https://iit.tn/` (42 Documents Markdown & Synchroniseur `sync_iit_corpus.py`)
1. **Collecte conforme (`backend/scripts/scrape_iit_tn.py`) :** Extrait de manière déterministe et respectueuse de `robots.txt` **42 pages officielles en français** depuis `https://iit.tn/` (cycles ingénieur GLID/SDIA/ARSI/Génie Civil/Industriel/Mécanique/Procédés, licences, cycle préparatoire, admissions et vie universitaire) dans `backend/data/corpus/iit/*.md` accompagnées de `backend/data/corpus/iit/manifest.json`.
2. **Synchronisation PostgreSQL idempotente (`backend/scripts/sync_iit_corpus.py`) :**
   ```bash
   docker compose exec -T backend python scripts/sync_iit_corpus.py
   ```
   - **Exécution initiale :** Enregistre les 42 documents (`status = 'PUBLISHED'`, `v1`, `PENDING INGEST`).
   - **Ré-exécutions sans changement :** Détecte `UNCHANGED` par `SHA-256` sur les 42 entrées (`skipped_unchanged = 42`, `jobs_enqueued = 0`).
   - **Mise à jour d'une page `iit.tn` :** Crée automatiquement `v{N+1}` et un unique job `PENDING REINDEX`.

---

## 7. Règles d'Accès RBAC Associées aux Tables Documentaires

| Stade / Rôle | `core.documents` | `core.document_versions` | `core.document_sync_jobs` & `logs` | `knowledge.chunks` (`vector(1024)`) |
| :--- | :---: | :---: | :---: | :---: |
| **`VISITOR`** (`guest` / `visitor` / `visiteur`) | Lecture (`PUBLISHED` via API) | — | — | Lecture hybride RAG (`is_active = TRUE`) |
| **`CANDIDATE`** (`candidate` / `candidat` / `student`) | Lecture & téléchargement (`PUBLISHED`) | — | — | Lecture hybride RAG (`is_active = TRUE`) |
| **`RESPONSABLE`** (`responsable`, `school_scope`) | **CRUD complet (école assignée)** | **Création, historique, activation/rollback (école assignée)** | **Consultation statuts & logs (école assignée)** | Lecture RAG & déclenchement sync |
| **`HUMAN_RH`** (`rh`, support) | Lecture seule | Lecture historique | Lecture diagnostique | Lecture hybride RAG |
| *Legacy `direction` / `admin`* | `direction` : lecture seule (`403` en écriture) · `admin` : maintenance technique | `direction` : lecture · `admin` : maintenance | `direction` : lecture · `admin` : maintenance | Maintenance & réindexation (`admin`) |
