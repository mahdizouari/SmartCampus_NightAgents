# SmartCampus AI — Contrat d'Interface & Spécification REST OpenAPI (`SA-5.2`)

> **Tâche :** `[SA-5]` Concevoir les API et les règles d'accès — Contrat d'API REST & Schémas (aligné Sprint 1 + Sprint 2)  
> **Référence associée :** [rbac_matrix.md](./rbac_matrix.md) (`SA-5.1`)  
> **Routeurs FastAPI actifs :** `backend/app/api/v1/router.py` (`/auth`, `/chat`, `/voice-notes`, `/catalog`, `/documents`, `/knowledge/documents`, `/health`)  
> **Standard :** OpenAPI Specification v3.1 / RFC 7807 (Problem Details)

---

## 1. Conventions Générales & Format des Échanges

### 1.1 Base URLs, Préfixes & Variables d'Environnement

L'ensemble des URLs de l'API est piloté par variables d'environnement (`.env`) côté backend et frontend :

* **Développement Local (Docker Compose / Uvicorn) :**
  * Backend API : `http://localhost:8000/api/v1` (`API_V1_STR=/api/v1`)
  * Frontend Next.js : `http://localhost:3000` (consomme `NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1`)
  * Sondes techniques de vie : `GET /health`, `GET /health/db`, `GET /health/redis` (également exposées sous `/api/v1/health*`)
  * Documentation interactive : `http://localhost:8000/docs` (Swagger UI) et `/redoc`
* **Déploiement Cible :** `https://${API_DOMAIN}/api/v1` (aucune URL n'est figée en dur dans le code applicatif).

### 1.2 Découpage des Préfixes par Domaine Métier (`/api/v1/*`)

**Modules actifs implémentés dans `backend/app/api/v1/router.py` :**
* `/api/v1/auth` : Sessions invités signées (`VISITOR`, TTL 7j), OTP email/SMS, Google OAuth 2.0, **fusion automatique Redis -> PostgreSQL (`VISITOR -> CANDIDATE`)**, rafraîchissement JWT et consentement INPDP (Loi 2004-63).
* `/api/v1/chat` : Dialogue multilingue (FR, Fusha, Derja, Arabizi) orchestré par le superviseur LangGraph `VISION` (`qwen3:8b` `/no_think` / `smartcampus-vision-8b` sur Ollama local).
* `/api/v1/voice-notes` : Transcription vocale locale sans clé (`WHISPER_PROVIDER=local`, `whisper-large-v3`) avec relecture obligatoire avant exécution par `VISION`.
* `/api/v1/catalog` : Consultation publique (`GET`) et gestion CRUD cloisonnée par école pour le `Responsable` (`POST / PATCH / DELETE` via `require_catalog_manager` ; registre racine `/schools` via `require_catalog_admin`).
* `/api/v1/documents` : Extraction VLM locale sans clé (`qwen2.5vl:7b` sur Ollama) des relevés tunisiens (Bac & Licence) et synchronisation au profil PostgreSQL (`Bonus-1` & `Bonus-2`).
* `/api/v1/knowledge/documents` : Gestion du corpus officiel RAG (`SA-49` / `SA-50` / `SA-56`), détection de changements `SHA-256`, versioning immuable, promotion/rollback et suivi des jobs `bge-m3` (`vector(1024)`), alimenté notamment par les 42 documents officiels `https://iit.tn/` (`backend/data/corpus/iit/` + `backend/scripts/sync_iit_corpus.py`).

**Modules cibles complémentaires (Sprint 2 / Sprint 3) :**
* `/api/v1/orientation` : Règles d'éligibilité YAML (`1.3_Gatekeeper`) et `score_compatibilite()` (`1.5_Advisor`).
* `/api/v1/enrolment` : Soumission atomique de préinscription (`core` + `<school>.dossiers` + `core.outbox`) et QCM ciblé (`1.6_Registrar`).
* `/api/v1/scheduling` : Créneaux `FREE / HELD / BOOKED` (`SELECT ... FOR UPDATE`) et reports plafonnés à 2 (`JARVIS 2.1_Scheduler`).
* `/api/v1/rh` : File d'escalade `Human RH` (`owner in {JARVIS, RH:<user>}`).
* `/api/v1/responsable` : Disponibilités d'entretiens, suivi des dossiers/briefs 1 page et **décision finale HITL signée (`ADMITTED | WAITLIST | REJECTED`)**.

### 1.3 En-têtes Standards & Authentification Progressive
* `Content-Type: application/json` (ou `multipart/form-data` pour l'envoi de fichiers audio/PDF/images/corpus)
* `Cookie: smartcampus_guest_session=<signed_token>` ou `X-Session-ID: <UUIDv4>` : Identifie un **`VISITOR`** sans compte (`guest` / `visitor` / `visiteur`).
* `Authorization: Bearer <JWT>` : Identifie un **`CANDIDATE`** (`role` dans `candidate`, `candidat`, `student`), un **`RESPONSABLE`** (`role: responsable` + `school_scope: iit | isb | isa`), ou un agent support `HUMAN_RH` (`role: rh`). Tout appelant authentifié est lié strictement à son identité JWT (anti-IDOR).
* `X-Request-ID: <UUIDv4>` : Identifiant de corrélation distribuée.

### 1.4 Format Uniforme des Erreurs API

```json
{
  "error_code": "VALIDATION_ERROR",
  "status": 422,
  "message": "Unprocessable Entity",
  "detail": "Field 'declared_average' must be between 0 and 20.",
  "instance": "/api/v1/orientation/check-eligibility",
  "invalid_params": [
    {
      "name": "declared_average",
      "reason": "ensure this value is less than or equal to 20"
    }
  ]
}
```

---

## 2. Définitions TypeScript Clés (`frontend/src/types/index.ts` & Contrats Partagés)

```typescript
// --- Stade Canonique de l'Acteur ---
export type ActorStage = 'VISITOR' | 'CANDIDATE' | 'RESPONSABLE';

// --- États du Candidat (16 valeurs PostgreSQL exactes) ---
export type CandidateStatus =
  | 'ANONYMOUS'
  | 'PROFILING'
  | 'ELIGIBLE'
  | 'INELIGIBLE'
  | 'ORIENTED'
  | 'AUTHENTICATED'
  | 'PREINSCRIBED'
  | 'QCM_DONE'
  | 'SCHEDULED'
  | 'INTERVIEWED'
  | 'ADMITTED'
  | 'WAITLIST'
  | 'REJECTED'
  | 'DORMANT'
  | 'ESCALATED'
  | 'WITHDRAWN';

// --- États des Créneaux & Rendez-vous ---
export type SlotStatus = 'FREE' | 'HELD' | 'BOOKED';

export type AppointmentStatus =
  | 'AWAITING_SLOT'
  | 'INVITED'
  | 'BOOKED'
  | 'REMINDED'
  | 'ATTENDED'
  | 'CANCELLED'
  | 'NO_SHOW'
  | 'ESCALATED'
  | 'WITHDRAWN';

// --- Établissements NAU ---
export type SchoolCode = 'iit' | 'isb' | 'isa';

// --- Décision HITL Exclusive du Responsable ---
export type AdmissionDecision = 'ADMITTED' | 'WAITLIST' | 'REJECTED';
```

---

## 3. Endpoints REST Détaillés par Module

### Module 1 : Authentification, Sessions `Visitor` & Fusion Automatique (`/auth`)

#### 1.1 `POST /auth/guest-session` *(ainsi que `GET`, `PATCH`, `DELETE /auth/guest-session`)*
* **Objectif :** Émettre ou rafraîchir une session anonyme Redis (TTL glissant 7 jours, cookie signé `smartcampus_guest_session`) pour un `Visiteur` (lycéen, diplômé ou parent/proche).
* **Response `201 Created` (`GuestSessionIssuedResponse`) :**
```json
{
  "session_id": "7f3b8b1a-9a6a-4d43-a664-984bb3014a02",
  "status": "ANONYMOUS",
  "actor_stage": "VISITOR",
  "expires_at": "2026-12-04T18:00:00Z",
  "ttl_seconds": 604800,
  "preferred_language": "FR",
  "step": "ANONYMOUS",
  "school_target": null,
  "turn_count": 0,
  "signed_cookie_issued": true,
  "session_data": {}
}
```

#### 1.2 `POST /auth/otp/send`
* **Objectif :** Envoyer un code OTP à 6 chiffres par email ou SMS (`destination` ou alias `email`).
* **Request Schema :**
```json
{
  "channel": "email",
  "destination": "candidat@example.tn"
}
```
* **Response `200 OK` (`OtpSendResponse`) :**
```json
{
  "success": true,
  "message": "OTP sent successfully",
  "expires_in_seconds": 300,
  "retry_after_seconds": 60,
  "dev_otp_code": null
}
```

#### 1.3 `POST /auth/otp/verify` *(et `POST /auth/oauth/google`, `POST /auth/refresh`, `GET /auth/me`)*
* **Objectif :** Valider le code OTP, **fusionner automatiquement** la session `Visitor` éventuelle (depuis `anonymous_session_id`, le cookie `smartcampus_guest_session` ou `X-Session-ID`), effacer le cookie invité et émettre les jetons JWT promouvant l'utilisateur au stade `CANDIDATE` (ou `RESPONSABLE` pour un compte personnel NAU).
* **Request Schema (`OtpVerifyRequest`) :**
```json
{
  "channel": "email",
  "destination": "candidat@example.tn",
  "code": "592814",
  "full_name": "Amine Trabelsi",
  "anonymous_session_id": "7f3b8b1a-9a6a-4d43-a664-984bb3014a02",
  "consent_merge": true
}
```
* **Response `200 OK` (`AuthTokenResponse`) :**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "actor_stage": "CANDIDATE",
  "session_merged": true,
  "turns_merged": 6,
  "user": {
    "id": "c1a2d3e4-5678-90ab-cdef-1234567890ab",
    "email": "candidat@example.tn",
    "full_name": "Amine Trabelsi",
    "fullName": "Amine Trabelsi",
    "role": "candidate",
    "actor_stage": "CANDIDATE",
    "school_scope": null,
    "school": null,
    "candidate_id": "9f8e7d6c-5432-10ab-cdef-1234567890ab",
    "status": "AUTHENTICATED",
    "isAnonymous": false
  }
}
```

#### 1.4 `POST /auth/merge-session`
* **Objectif :** Endpoint explicite idempotent de fusion d'une session `Visitor` Redis vers le compte `Candidate` PostgreSQL (`core.candidate`, `core.candidate_profile`, `core.guest_sessions`).
* **Response `200 OK` (`SessionMergeResponse`) :**
```json
{
  "candidate_id": "9f8e7d6c-5432-10ab-cdef-1234567890ab",
  "account_id": "c1a2d3e4-5678-90ab-cdef-1234567890ab",
  "anonymous_session_id": "7f3b8b1a-9a6a-4d43-a664-984bb3014a02",
  "actor_stage": "CANDIDATE",
  "turns_merged": 6,
  "declared_profile_synced": true,
  "current_status": "AUTHENTICATED",
  "merged_at": "2026-11-28T10:05:00Z",
  "message": "Conversation et profil invité associés avec succès au compte candidat."
}
```

#### 1.5 `POST /auth/consent`
* **Objectif :** Enregistrer le consentement explicite **Loi n° 2004-63 / INPDP** (`ORIENTATION_PROFILE`, `DOCUMENT_OCR`, `ADMISSION_FOLLOWUP`).
* **Request Schema (`ConsentCreateRequest`) :**
```json
{
  "terms_accepted": true,
  "privacy_policy_accepted": true,
  "transcript_processing_accepted": true,
  "policy_version": "2026.1"
}
```
* **Response `200 OK` (`ConsentResponse`) :**
```json
{
  "consent_id": "b0f7e8a1-4321-4def-89ab-9876543210ab",
  "logged_at": "2026-11-28T10:06:00Z",
  "status": "VALID",
  "purposes_granted": ["ORIENTATION_PROFILE", "DOCUMENT_OCR", "ADMISSION_FOLLOWUP"]
}
```

---

### Module 2 : Dialogue Multilingue `VISION` & Notes Vocales (`/chat`, `/voice-notes`)

#### 2.1 `POST /chat/message` *(et `GET /chat/health`, `GET /chat/evaluate-dialect`)*
* **Objectif :** Envoyer un tour texte au superviseur LangGraph `VISION` (`qwen3:8b` `/no_think` ou `smartcampus-vision-8b` sur Ollama local). Ouvert aux `VISITOR` (cookie/`X-Session-ID`) comme aux `CANDIDATE` (`Bearer JWT`).
* **Request Schema (`ChatMessageRequest`) :**
```json
{
  "message": "chnoma les filières d'ingénieur fel IIT ?",
  "language_hint": "auto",
  "campus_scope": "IIT"
}
```
* **Response `200 OK` (`ChatMessageResponse`) :**
```json
{
  "session_id": "7f3b8b1a-9a6a-4d43-a664-984bb3014a02",
  "sessionId": "7f3b8b1a-9a6a-4d43-a664-984bb3014a02",
  "reply_text": "Fi l'IIT fama plusieurs spécialités d'ingénieur habilitées...",
  "reply": "Fi l'IIT fama plusieurs spécialités d'ingénieur habilitées...",
  "sub_agent": "1.2_Librarian",
  "subAgent": "1.2_Librarian",
  "detected_language": "ar-TN-arabizi",
  "detectedDialect": "Arabizi",
  "intent": "INQUIRE_PROGRAMS",
  "citations": [
    {
      "id": "iit-formation-01",
      "documentTitle": "Formations IIT — Cycle Ingénieur",
      "source_document": "https://iit.tn/formation/",
      "school": "IIT",
      "program": "Cycle Ingénieur",
      "chunk_id": "chunk-iit-formation-01"
    }
  ],
  "suggested_actions": ["Vérifier mon éligibilité", "Comparer deux filières"],
  "actionPrompt": null,
  "provider": "ollama",
  "model_used": "qwen3:8b",
  "latency_ms": 940,
  "used_fallback": false,
  "comprehension_confidence": 0.92,
  "needs_reformulation": false,
  "reformulation_prompt": null,
  "guardrail_triggered": false,
  "guardrail_action": null,
  "actor_stage": "VISITOR",
  "candidate_status": "PROFILING"
}
```

#### 2.2 `POST /voice-notes/transcribe` & `POST /voice-notes/process`
* **Étape 1 (`POST /voice-notes/transcribe`, `multipart/form-data`) :** Transcrit un enregistrement vocal (`webm`, `mp4`, `m4a`, `mp3`, `wav`, `ogg` `<= 25 Mo`) via Whisper STT local sans clé (`WHISPER_PROVIDER=local`, `whisper-large-v3`) et retourne `VoiceNoteTranscribeResponse` avec `requires_review: true` **sans appeler le LLM avant validation utilisateur**.
* **Étape 2 (`POST /voice-notes/process`, `application/json`) :** Transmet la transcription relue/corrigée (`final_transcript`, `transcript_id`, `campus_scope`, `mode`) au superviseur `VISION` et retourne `VoiceNoteProcessResponse` (incluant `actor_stage` et `candidate_status`).

---

### Module 3 : Orientation & Recommandation Académique (`/orientation` — Contrat Sprint 2)

#### 3.1 `POST /orientation/check-eligibility`
* **Objectif :** Évaluer l'admissibilité déclarative via le moteur déterministe de règles YAML et `core.program_prerequisite` (`VISION 1.3_Gatekeeper`). Retourne `ELIGIBLE`, `BORDERLINE_TO_CONFIRM` (soumis à confirmation du `Responsable`) ou `INELIGIBLE`.

#### 3.2 `POST /orientation/recommend`
* **Objectif :** Calculer `score_compatibilite()` (exclusivement à partir des résultats académiques et intérêts déclarés) et renvoyer 2 à 3 parcours comparés avec citations issues de `knowledge.chunks` (`VISION 1.5_Advisor`).

---

### Module 4 : Extraction VLM des Relevés & Préinscription (`/documents`, `/enrolment`)

#### 4.1 Endpoints VLM Actifs (`Bonus-1` & `Bonus-2` — `backend/app/api/v1/endpoints/documents.py`)
* **Contrôle d'accès :** Exige `Authorization: Bearer <JWT>` (`role` dans `candidate`, `candidat`, `student` — restreints à leur propre `candidate_id` — ou personnel `responsable`, `rh`, `admin`). Rejette tout `VISITOR` avec `401 Unauthorized`.
* **`GET /documents/vlm/health` :** Retourne l'état du VLM local Ollama (`VLM_PROVIDER=ollama`, `VLM_MODEL=qwen2.5vl:7b`), la taille max (`10 Mo`) et les MIME supportés (`PNG`, `JPEG`, `WEBP`, `PDF`).
* **`POST /documents/extract-bac-transcript` (`multipart/form-data`) :** Extrait un relevé du Bac tunisien (`RELEVE_BAC`) ou universitaire de Licence (`RELEVE_UNIVERSITAIRE`) en `BacTranscriptExtractionResult` (masquage INPDP `student_name: null`, recalcul pondéré et drapeaux `< 0.85`).
* **`POST /documents/extract-and-sync-transcript` (`multipart/form-data`) :** Exécute en un seul appel l'extraction VLM (`qwen2.5vl:7b`) **et** la persistance PostgreSQL (`core.document`, `core.candidate_profile`, `core.declared_grades`, `core.audit_log`).
* **`POST /documents/sync-transcript-profile` (`application/json`) :** Persiste ou confirme (`confirmed_by_student: true`) un `BacTranscriptExtractionResult` existant (`CONFIRMED_BY_STUDENT`, sans jamais écraser un drapeau `MISMATCH_RH`).
* **`GET /documents/my-transcript-profile` :** Restitue le profil académique synchronisé, les documents VLM, les notes persistées et les drapeaux `low_confidence_fields`.

* **Response `200 OK` (`TranscriptProfileSyncResponse` sur `/documents/extract-and-sync-transcript`) :**
```json
{
  "candidate_id": "9f8e7d6c-5432-10ab-cdef-1234567890ab",
  "document_id": "d9e8f7a6-5432-10fe-dcba-0987654321ba",
  "document_type": "RELEVE_UNIVERSITAIRE",
  "diploma_stage": "L3",
  "verification_status": "PENDING",
  "profile_updated": true,
  "current_education_level": "LICENCE",
  "current_specialty": "Licence en Science de l'Informatique : Big Data et Analyse de Données",
  "declared_overall_avg": 12.98,
  "grades_persisted_count": 6,
  "overall_confidence": 0.96,
  "min_confidence": 0.91,
  "discrepancy_detected": false,
  "needs_manual_review": false,
  "low_confidence_flags": [],
  "warnings": [],
  "synced_at": "2026-11-28T10:12:00Z"
}
```

#### 4.2 `POST /enrolment/pre-inscribe` & `POST /enrolment/qcm/submit` *(Contrat Sprint 2/3)*
* **`POST /enrolment/pre-inscribe` :** Écriture atomique dans `core` + `<school>.dossiers` (`iit | isb | isa`), passage à `PREINSCRIBED` et insertion de `"pre-inscription submitted"` dans `core.outbox`.
* **`POST /enrolment/qcm/submit` :** Évaluation déterministe de 5 à 10 questions issues de la banque approuvée, génération du brief 1 page (`<school>.briefs`), passage à `QCM_DONE` et événement `core.outbox` `"qcm_completed"`.

---

### Module 5 : Prise de Rendez-vous & Relances (`/scheduling` — Contrat Sprint 3)

* **`GET /scheduling/slots` :** Créneaux `FREE` publiés par le `Responsable` de l'école cible.
* **`POST /scheduling/appointments/book` :** Verrouillage atomique `SELECT ... FOR UPDATE` + `UNIQUE(responsable_id, start_time)` (`JARVIS 2.1_Scheduler`).
* **`POST /scheduling/appointments/{id}/reschedule` :** Plafonné à **2 reports maximum** (`reschedules <= 2`) ; à la 3e demande, retourne `422 RESCHEDULE_LIMIT_EXCEEDED` et ouvre automatiquement un ticket `core.rh_ticket`.

---

### Module 6 : Support d'Exceptions RH (`/rh` — Contrat Sprint 3)

* **`GET /rh/tickets`**, **`PATCH /rh/tickets/{id}/claim`**, **`POST /rh/tickets/{id}/resolve` :** Gestion des cas d'exception par `Human RH`. Le `claim` bascule `owner = "RH:<user_id>"` et suspend immédiatement les relances automatisées de `JARVIS`.

---

### Module 7 : Portail `Responsable` & Décision HITL Exclusive (`/responsable`)

* **`POST /responsable/slots` :** Publication des créneaux d'entretien par le `Responsable` (`school_scope` `iit | isb | isa`).
* **`GET /responsable/dossiers` & `GET /responsable/dossiers/{id}` *(alias candidat : `GET /responsable/candidates/{id}/brief`)* :** Consultation des dossiers de l'école du `Responsable`, du score QCM, des alertes VLM et du brief d'entretien d'1 page.
* **`POST /responsable/dossiers/{id}/decision` & `POST /responsable/candidates/{id}/decision` *(HITL Exclusif)* :**
  * **Note d'alignement Frontend <-> Backend :** Le service frontend (`frontend/src/services/responsable-service.ts`) cible la ressource dossier `/responsable/dossiers/{dossierId}/decision` tandis que les diagrammes de gouvernance référencent également `/responsable/candidates/{candidateId}/decision`. Lors de l'implémentation backend Sprint 3, le routeur `/responsable` exposera les deux routes (résolvant de façon équivalente le dossier actif de l'école du `Responsable`) sous le même verrou RBAC + RLS strict : **seul `role == "responsable"` avec `school_scope` correspondant à l'école du dossier peut signer `ADMITTED`, `WAITLIST` ou `REJECTED`** (tout agent IA ou autre rôle reçoit `403 Forbidden`).

---

### Module 8 : Catalogue Académique — Consultation Publique & CRUD `Responsable` (`/catalog`)

#### 8.1 Endpoints Publics Actifs (`backend/app/api/v1/endpoints/catalog.py`)
* **`GET /catalog/schools?is_active=true` :** Liste `list[PublicSchoolResponse]` (`iit`, `isb`, extension `isa`).
* **`GET /catalog/programs` :** Filtres `school_code`, `degree_level`, `study_mode`, `search`, `is_active`, `limit`, `offset`. Retourne `ProgramListResponse` (`{ total, items }`) :
```json
{
  "total": 16,
  "items": [
    {
      "id": "4e8c9d10-1234-4a5b-8c9d-0123456789ab",
      "code": "IIT-ING-GLID",
      "school_code": "iit",
      "title": "Génie Logiciel et Informatique Décisionnelle",
      "degree_level": "cycle_ingenieur",
      "degree_title": "Diplôme National d'Ingénieur",
      "duration_years": 3,
      "study_modes": ["plein_temps"],
      "description": "Formation d'ingénieurs en architecture logicielle, données et IA.",
      "source_document": "https://iit.tn/formation/genie-logiciel-et-informatique-decisionnelle/",
      "is_active": true
    }
  ]
}
```
* **`GET /catalog/programs/compare?program_ids=IIT-ING-GLID&program_ids=IIT-ING-SDIA` :** Comparaison côte à côte (`2` à `4` formations).
* **`GET /catalog/programs/{program_id}`**, **`GET /catalog/programs/{program_id}/prerequisites`**, **`GET /catalog/programs/{program_id}/fees`**, **`GET /catalog/campaigns`**.

#### 8.2 Endpoints CRUD de Gestion du Catalogue (`backend/app/api/v1/endpoints/catalog_management.py`)
* **CRUD Formations, Prérequis, Frais & Campagnes (`Depends(require_catalog_manager)`) :** Autorisé au **`Responsable`** cloisonné sur son `school_scope` (`iit | isb | isa`) ainsi qu'au rôle technique `admin` :
  * `POST /catalog/programs`, `PATCH /catalog/programs/{program_id}`, `DELETE /catalog/programs/{program_id}`
  * `POST /catalog/programs/{program_id}/prerequisites`, `PATCH /catalog/prerequisites/{prerequisite_id}`, `DELETE /catalog/prerequisites/{prerequisite_id}`
  * `POST /catalog/programs/{program_id}/fees`, `PATCH /catalog/fees/{fee_id}`, `DELETE /catalog/fees/{fee_id}`
  * `POST /catalog/campaigns`, `PATCH /catalog/campaigns/{campaign_id}`, `DELETE /catalog/campaigns/{campaign_id}`
* **Registre racine d'écoles (`Depends(require_catalog_admin)`) :** `POST /catalog/schools` et `PATCH /catalog/schools/{school_code}` sont réservés exclusivement au rôle racine `admin`.

---

### Module 9 : Gestion du Corpus Officiel RAG & Synchronisation (`/knowledge/documents`)

Implémenté dans `backend/app/api/v1/endpoints/knowledge.py` (`SA-49`, `SA-50`, `SA-56`) et adossé à `core.documents`, `core.document_versions`, `core.document_sync_jobs` (`embedding_model = 'bge-m3'`) et `knowledge.chunks` (`vector(1024)`) :

* **`GET /knowledge/documents` :** Liste paginée (`{ items, total, page, page_size }`) filtrable par `school_code` (`iit | isb | isa | nau_common`), `index_status` et `is_published`.
* **`POST /knowledge/documents` (`multipart/form-data`) :** Création/upload d'un document officiel (`code` `^[A-Za-z0-9._-]+$`, `title`, `school_code`, `doc_type`, `is_published`, `file` ou `content`). Réservé à `responsable` (cloisonné à son `school_scope`) et `admin` (`direction` reçoit `403 Forbidden`).
* **`POST /knowledge/documents/check-changes` :** Simulation dry-run `SHA-256` et diff métadonnées/texte sans écriture DB.
* **`GET /knowledge/documents/{document_id}`**, **`PATCH /knowledge/documents/{document_id}`**, **`DELETE /knowledge/documents/{document_id}`** (`?purge_storage=false`).
* **`GET /knowledge/documents/{document_id}/download`** & **`GET /knowledge/documents/{document_id}/versions/{version_number}/download`** (avec en-tête `ETag` `SHA-256`).
* **`POST /knowledge/documents/{document_id}/versions`**, **`GET /knowledge/documents/{document_id}/versions`**, **`POST /knowledge/documents/{document_id}/versions/{version_number}/activate`** (promotion atomique ou rollback).
* **`GET /knowledge/documents/{document_id}/sync-jobs`** & **`GET /knowledge/documents/sync-jobs/{job_id}/logs`**.
* **Synchronisation CLI du corpus officiel `https://iit.tn/` :** Les 42 documents Markdown scrapés dans `backend/data/corpus/iit/` sont synchronisés de manière idempotente via `backend/scripts/sync_iit_corpus.py`.
