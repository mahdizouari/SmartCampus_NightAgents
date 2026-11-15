# SmartCampus AI — Contrat d'Interface & Spécification REST OpenAPI (`SA-5.2`)

> **Tâche :** `[SA-5]` Concevoir les API et les règles d'accès — Contrat d'API REST & Schémas  
> **Référence associée :** [rbac_matrix.md](./rbac_matrix.md) (`SA-5.1`)  
> **Standard :** OpenAPI Specification v3.1 / RFC 7807 (Problem Details)

---

## 1. Conventions Générales & Format des Échanges

### 1.1 Base URLs, Préfixes & Variables d'Environnement

L'ensemble des URLs de l'API est piloté par des variables d'environnement (`.env`) côté backend et frontend :

* **Développement Local (Docker / Uvicorn) :**
  * Backend API : `http://localhost:8000/api/v1` (`API_BASE_URL`)
  * Frontend Next.js : `http://localhost:3000` (consomme `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`)
  * Sonde technique de vie : `GET /health` sur `http://localhost:8000/health` (non versionné, pour Docker Compose / orchestrateur)
  * Documentation interactive : `http://localhost:8000/docs` (Swagger UI) et `/redoc`

* **Environnement Cible de Déploiement / Production :**
  * `https://${API_DOMAIN}/api/v1` (valeur indicative conventionnelle : `https://api.smartcampus.nau.tn/api/v1`)
  * > [!NOTE]
  * > `TODO(open-question U11)` : Le nom de domaine officiel de production sera confirmé lors de la sélection de l'hébergement (Cloud GPU vs infrastructure interne NAU). L'application ne fige aucune URL en dur.

### 1.2 Découpage des Préfixes par Domaine Métier (`/api/v1/*`)
Le préfixe `/api/v1` garantit le versioning strict de l'API sans rupture pour les clients :
* `/api/v1/auth` : Sessions invités, OTP, OAuth Google, fusion Redis/PostgreSQL, consentement INPDP.
* `/api/v1/chat` : Dialogue multimodal (texte/vocal Whisper), dispatch superviseur `VISION` et RAG sourcé.
* `/api/v1/orientation` : Moteur de règles d'éligibilité YAML (`1.3 Gatekeeper`) et scoring de compatibilité (`1.5 Advisor`).
* `/api/v1/enrolment` & `/api/v1/documents` : Téléversement MinIO relevés de notes, extraction VLM, préinscription atomique et QCM.
* `/api/v1/scheduling` : Créneaux d'entretien, réservation `SELECT FOR UPDATE` sans double-booking, et reports (max 2).
* `/api/v1/rh` : File de tickets d'escalade RH, assignation manuelle (suspendant JARVIS), et arbitrage d'anomalies.
* `/api/v1/responsable` : Disponibilités responsables, fiches 1 page et **décision finale HITL signée**.
* `/api/v1/catalog` : Consultation publique du catalogue des formations IIT, ISB et extension ISA.

### 1.3 En-têtes Standards
* `Content-Type: application/json` (ou `multipart/form-data` pour l'envoi de fichiers)
* `Accept: application/json`
* `X-Session-ID: <UUIDv4>` : Requis pour les requêtes non authentifiées (`ANONYMOUS_GUEST`)
* `Authorization: Bearer <JWT>` : Requis pour les acteurs authentifiés (`AUTHENTICATED_STUDENT`, `HUMAN_RH`, `NAU_RESPONSABLE`, `DIRECTION`)
* `X-Request-ID: <UUIDv4>` : Identifiant de traçabilité distribuée (généré par le client ou injecté par le middleware)

### 1.4 Format Uniforme des Erreurs API

Toutes les réponses d'erreur HTTP (`4xx`, `5xx`) adoptent un format JSON standardisé, typé et directement exploitable par le frontend (évitant les URLs verbeuses au profit de codes d'erreur machine-readable clairs) :

```json
{
  "error_code": "VALIDATION_ERROR",
  "status": 422,
  "message": "Unprocessable Entity",
  "detail": "Le champ 'declared_average' doit être compris entre 0 et 20.",
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

## 2. Définitions TypeScript Clés (Front & Shared Contracts)

```typescript
// --- États du Candidat ---
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

// --- Décision HITL d'Admission ---
export type AdmissionDecision = 'ADMITTED' | 'WAITLIST' | 'REJECTED';

// --- Format Standard de Réponse d'Erreur ---
export interface ApiErrorResponse {
  error_code: string;
  status: number;
  message: string;
  detail: string;
  instance: string;
  invalid_params?: Array<{
    name: string;
    reason: string;
  }>;
}
```

---

## 3. Endpoints REST Détaillés par Module

### Module 1 : Authentification & Gestion des Sessions (`/auth`)

#### 1.1 `POST /auth/guest-session`
* **Objectif :** Générer une session anonyme Redis (TTL 7 jours) pour un nouveau visiteur sans barrière d'inscription.
* **Acteur :** `ANONYMOUS_GUEST`
* **Response `201 Created` :**
```json
{
  "session_id": "7f3b8b1a-9a6a-4d43-a664-984bb3014a02",
  "status": "ANONYMOUS",
  "expires_at": "2026-11-22T18:00:00Z",
  "ttl_seconds": 604800
}
```

#### 1.2 `POST /auth/otp/send`
* **Objectif :** Expédier un code à 6 chiffres par email ou SMS pour amorcer la connexion / inscription progressive.
* **Request Schema :**
```json
{
  "channel": "email",
  "destination": "etudiant@example.tn"
}
```
* **Response `200 OK` :**
```json
{
  "message": "OTP sent successfully",
  "expires_in_seconds": 300,
  "retry_after_seconds": 60
}
```

#### 1.3 `POST /auth/otp/verify`
* **Objectif :** Valider le code OTP et délivrer les jetons Bearer JWT.
* **Request Schema :**
```json
{
  "channel": "email",
  "destination": "etudiant@example.tn",
  "code": "592814"
}
```
* **Response `200 OK` :**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "id": "c1a2d3e4-5678-90ab-cdef-1234567890ab",
    "email": "etudiant@example.tn",
    "role": "student",
    "status": "AUTHENTICATED"
  }
}
```

#### 1.4 `POST /auth/merge-session`
* **Objectif :** Fusionner atomiquement l'historique conversationnel et le profil déclaré de la mémoire Redis vers la base PostgreSQL (`core.candidate`, `core.candidate_profile`, `core.shortlist`).
* **Headers :** `Authorization: Bearer <token>`, `X-Session-ID: <UUIDv4>`
* **Request Schema :**
```json
{
  "anonymous_session_id": "7f3b8b1a-9a6a-4d43-a664-984bb3014a02"
}
```
* **Response `200 OK` :**
```json
{
  "candidate_id": "c1a2d3e4-5678-90ab-cdef-1234567890ab",
  "turns_merged": 8,
  "declared_profile_synced": true,
  "current_status": "AUTHENTICATED"
}
```

#### 1.5 `POST /auth/consent`
* **Objectif :** Consigner le consentement explicite conforme à la Loi n° 2004-63 / INPDP (préalable obligatoire au dépôt de relevé).
* **Request Schema :**
```json
{
  "terms_accepted": true,
  "privacy_policy_accepted": true,
  "transcript_processing_accepted": true,
  "policy_version": "2026.1"
}
```
* **Response `200 OK` :**
```json
{
  "consent_id": "b0f7e8a1-4321-4def-89ab-9876543210ab",
  "logged_at": "2026-11-15T18:05:00Z",
  "status": "VALID"
}
```

---

### Module 2 : Dialogue Multimodal & RAG (`/chat`)

#### 2.1 `POST /chat/message`
* **Objectif :** Échanger avec le superviseur LangGraph `VISION`. Prend en charge le texte et l'audio vocal (Whisper STT multilingue FR, Derja tunisienne, Arabizi via `1.1 Listener`).
* **Headers :** `X-Session-ID: <UUIDv4>` ou `Authorization: Bearer <token>`
* **Content-Type :** `application/json` ou `multipart/form-data`
* **Request Schema (JSON) :**
```json
{
  "message": "chnoma les spécialités mte3 cycle ingénieur fi IIT ?",
  "language_hint": "auto"
}
```
* **Response `200 OK` :**
```json
{
  "reply_text": "L'IIT propose plusieurs spécialités en cycle d'ingénieur habilitées par l'État et reconnues à l'international : Génie Logiciel & Informatique, Génie Industriel, Génie Civil, et Génie Mécatronique...",
  "detected_language": "ar-TN-arabizi",
  "intent": "INQUIRE_SPECIALTIES",
  "citations": [
    {
      "school": "IIT",
      "program": "Cycle Ingénieur",
      "source_document": "Brochure_IIT_2026_2027.pdf",
      "page": 6,
      "chunk_id": "iit-ing-general-01"
    }
  ],
  "suggested_actions": [
    "Voir les conditions d'accès",
    "Vérifier mon éligibilité",
    "Consulter les frais de scolarité"
  ],
  "candidate_status": "PROFILING"
}
```

#### 2.2 `GET /chat/history`
* **Objectif :** Récupérer les 6 à 8 derniers tours pour réhydrater l'interface de messagerie.
* **Response `200 OK` :**
```json
{
  "turns": [
    { "sender": "user", "text": "chnoma les spécialités...", "timestamp": "2026-11-15T18:05:00Z" },
    { "sender": "assistant", "text": "L'IIT propose plusieurs...", "timestamp": "2026-11-15T18:05:02Z", "citations": ["Brochure_IIT_2026_2027.pdf"] }
  ],
  "memory_tier": "HOT_REDIS"
}
```

---

### Module 3 : Orientation & Recommandation Académique (`/orientation`)

#### 3.1 `POST /orientation/check-eligibility`
* **Objectif :** Évaluer l'admissibilité déclarative via le moteur déterministe de règles YAML (`VISION 1.3 Gatekeeper`).
* **Request Schema :**
```json
{
  "current_degree": "Licence Informatique",
  "declared_average": 13.75,
  "graduation_year": 2026,
  "institution_origin": "ISIMS",
  "target_school": "IIT",
  "target_program": "Cycle Ingénieur Informatique"
}
```
* **Response `200 OK` :**
```json
{
  "eligibility_status": "ELIGIBLE",
  "rule_evaluated": "YAML: rules/iit_engineering_direct.yml",
  "min_threshold_required": 12.0,
  "candidate_score": 13.75,
  "requires_equivalence_review": false,
  "comment": "Profil admissible sur titre en 1ère année ingénieur."
}
```

#### 3.2 `POST /orientation/recommend`
* **Objectif :** Calculer le `score_compatibilite()` et renvoyer une shortlist de 2 à 3 parcours (`VISION 1.5 Advisor`).
* **Request Schema :**
```json
{
  "interests": ["Intelligence Artificielle", "Développement Web", "Data Science"],
  "career_goals": "Ingénieur Data / IA",
  "study_mode": "Plein temps"
}
```
* **Response `200 OK` :**
```json
{
  "shortlist": [
    {
      "track_id": "IIT-ING-GL",
      "school": "IIT",
      "program_name": "Cycle Ingénieur en Génie Logiciel",
      "compatibility_score": 94,
      "score_breakdown": {
        "academic_prerequisites": 95,
        "interest_alignment": 93
      },
      "strengths": ["Projets IA appliqués dès la 2ème année", "Partenariats industriels forts"],
      "citations": ["Catalogue_IIT_2026.pdf#L45"]
    },
    {
      "track_id": "ISB-MAS-BI",
      "school": "ISB",
      "program_name": "Master Professionnel Business Intelligence",
      "compatibility_score": 87,
      "score_breakdown": {
        "academic_prerequisites": 88,
        "interest_alignment": 86
      },
      "strengths": ["Synergie Data & Décision managériale"],
      "citations": ["Catalogue_ISB_2026.pdf#L12"]
    }
  ],
  "candidate_status": "ORIENTED"
}
```

---

### Module 4 : Documents & Préinscription Transactionnelle (`/enrolment`, `/documents`)

#### 4.1 `POST /documents/transcript`
* **Objectif :** Stocker le relevé officiel chiffré dans MinIO et lancer l'extraction VLM (`VISION 1.4 Reader`).
* **Headers :** `Authorization: Bearer <token>` (`role: student`)
* **Content-Type :** `multipart/form-data`
* **Response `202 Accepted` :**
```json
{
  "document_id": "d9e8f7a6-5432-10fe-dcba-0987654321ba",
  "file_name": "releve_l3_officiel.pdf",
  "storage_path": "core/documents/2026/c1a2d3e4/releve_l3_officiel.pdf",
  "extraction_status": "PROCESSING",
  "poll_url": "/api/v1/documents/d9e8f7a6-5432-10fe-dcba-0987654321ba/extraction"
}
```

#### 4.2 `GET /documents/{document_id}/extraction`
* **Objectif :** Consulter les notes extraites par VLM avec indice de confiance par champ.
* **Response `200 OK` :**
```json
{
  "document_id": "d9e8f7a6-5432-10fe-dcba-0987654321ba",
  "extraction_status": "COMPLETED",
  "overall_confidence": 0.95,
  "extracted_data": {
    "student_name": "Foulen Ben Foulen",
    "institution": "ISIMS Sfax",
    "academic_year": "2025/2026",
    "calculated_average": 13.82,
    "declared_average": 13.75,
    "confidence_average": 0.98,
    "subjects": [
      { "name": "Algorithmique Avancée", "grade": 15.5, "confidence": 0.97 },
      { "name": "Systèmes Répartis", "grade": 14.0, "confidence": 0.94 }
    ]
  },
  "discrepancy_detected": false
}
```

#### 4.3 `POST /enrolment/pre-inscribe`
* **Objectif :** Soumission définitive. Réalise une **écriture transactionnelle atomique** dans `core` et le schéma de l'école ciblée (`iit`, `isb` ou `isa`), passe à `PREINSCRIBED` et dépose l'événement `pre-inscription submitted` dans `core.outbox`.
* **Request Schema :**
```json
{
  "target_school": "iit",
  "selected_track_id": "IIT-ING-GL",
  "document_id": "d9e8f7a6-5432-10fe-dcba-0987654321ba",
  "confirmed_profile": {
    "first_name": "Foulen",
    "last_name": "Ben Foulen",
    "phone": "+216 98 123 456",
    "bac_section": "Sciences Informatiques",
    "last_diploma": "Licence en Informatique"
  }
}
```
* **Response `201 Created` :**
```json
{
  "dossier_id": "dos_iit_2026_0042",
  "candidate_status": "PREINSCRIBED",
  "outbox_event_id": "evt_outbox_88319",
  "next_step": "QCM_EVALUATION"
}
```

#### 4.4 `POST /enrolment/qcm/submit`
* **Objectif :** Valider les réponses du QCM (5–10 questions) et générer la synthèse pour l'évaluateur (`VISION 1.6 Registrar`).
* **Request Schema :**
```json
{
  "dossier_id": "dos_iit_2026_0042",
  "answers": [
    { "question_id": "Q101", "selected_option": "B" },
    { "question_id": "Q102", "selected_option": "A" }
  ]
}
```
* **Response `200 OK` :**
```json
{
  "dossier_id": "dos_iit_2026_0042",
  "score": 80.0,
  "total_questions": 10,
  "candidate_status": "QCM_DONE",
  "brief_ready": true,
  "next_step": "SCHEDULE_INTERVIEW"
}
```

---

### Module 5 : Prise de Rendez-vous & Relances (`/scheduling`)

#### 5.1 `GET /scheduling/slots`
* **Objectif :** Consulter les créneaux libres (`status = FREE`) pour l'école du dossier.
* **Query Params :** `school=iit&from_date=2026-11-16&to_date=2026-11-23`
* **Response `200 OK` :**
```json
{
  "slots": [
    {
      "slot_id": "slt_7721",
      "school": "IIT",
      "start_time": "2026-11-18T10:00:00Z",
      "end_time": "2026-11-18T10:30:00Z",
      "status": "FREE"
    }
  ]
}
```

#### 5.2 `POST /scheduling/appointments/book`
* **Objectif :** Réserver un créneau. Verrouillage atomique via `SELECT ... FOR UPDATE` et contrainte `unique(responsable_id, start_time)` évitant tout double-booking (`JARVIS 2.1 Scheduler`).
* **Request Schema :**
```json
{
  "slot_id": "slt_7721",
  "dossier_id": "dos_iit_2026_0042"
}
```
* **Response `201 Created` :**
```json
{
  "appointment_id": "apt_9912",
  "slot_id": "slt_7721",
  "status": "BOOKED",
  "scheduled_at": "2026-11-18T10:00:00Z",
  "reschedule_count": 0,
  "candidate_status": "SCHEDULED"
}
```

#### 5.3 `POST /scheduling/appointments/{id}/reschedule`
* **Objectif :** Reprogrammer un entretien. Plafonné à **2 reports maximum**. À la 3ᵉ tentative, l'API bloque l'automatisation et transfère le dossier à `Human RH`.
* **Request Schema :**
```json
{
  "new_slot_id": "slt_8814",
  "reason": "Empêchement académique"
}
```
* **Response `200 OK` (Si `reschedule_count < 2`) :**
```json
{
  "appointment_id": "apt_9912",
  "new_slot_id": "slt_8814",
  "reschedule_count": 1,
  "max_allowed": 2,
  "status": "BOOKED"
}
```
* **Response `422 Unprocessable Entity` (Si `reschedule_count >= 2` $\rightarrow$ Escalade) :**
```json
{
  "error_code": "RESCHEDULE_LIMIT_EXCEEDED",
  "status": 422,
  "message": "Limite de reports atteinte",
  "detail": "Nombre maximum de reports (2) atteint. Le dossier a été transmis à la cellule RH pour accompagnement personnalisé.",
  "instance": "/api/v1/scheduling/appointments/apt_9912/reschedule",
  "ticket_id": "rh_tkt_5041",
  "owner": "RH:unassigned"
}
```

---

### Module 6 : Cellule d'Exceptions RH (`/rh`)

#### 6.1 `GET /rh/tickets`
* **Objectif :** File de traitement des anomalies et dossiers bloqués pour `Human RH`.
* **Headers :** `Authorization: Bearer <token>` (`role: rh`)
* **Response `200 OK` :**
```json
{
  "total": 1,
  "tickets": [
    {
      "ticket_id": "rh_tkt_5041",
      "candidate_id": "c1a2d3e4-5678-90ab-cdef-1234567890ab",
      "school": "IIT",
      "reason_code": "RESCHEDULE_EXCEEDED",
      "priority": "HIGH",
      "owner": "RH:unassigned",
      "created_at": "2026-11-15T18:10:00Z",
      "summary_5_lines": "1. Candidat préinscrit en Génie Logiciel IIT.\n2. Relevé validé (moyenne 13.82).\n3. QCM passé avec score 80%.\n4. A sollicité 3 reports successifs.\n5. Action : Fixer manuellement une date ferme ou clôturer."
    }
  ]
}
```

#### 6.2 `PATCH /rh/tickets/{id}/claim`
* **Objectif :** Prise de propriété par un agent RH humain (`owner = RH:<user_id>`). Suspend immédiatement toute relance automatique de `JARVIS`.
* **Response `200 OK` :**
```json
{
  "ticket_id": "rh_tkt_5041",
  "owner": "RH:m.triki",
  "automated_reminders_paused": true,
  "claimed_at": "2026-11-15T18:15:00Z"
}
```

#### 6.3 `POST /rh/tickets/{id}/resolve`
* **Objectif :** Résolution du ticket et restitution optionnelle de la main à `JARVIS`.
* **Request Schema :**
```json
{
  "resolution_code": "APPOINTMENT_FIXED_MANUALLY",
  "handover_to_jarvis": true,
  "notes": "Rendez-vous calé par appel téléphonique pour le 20/11."
}
```
* **Response `200 OK` :**
```json
{
  "ticket_id": "rh_tkt_5041",
  "status": "RESOLVED",
  "owner": "JARVIS"
}
```

---

### Module 7 : Portail Responsable NAU & Décision HITL (`/responsable`)

#### 7.1 `POST /responsable/slots`
* **Objectif :** Déclaration de disponibilités d'entretien par le responsable de l'école.
* **Headers :** `Authorization: Bearer <token>` (`role: responsable`, `school: iit`)
* **Request Schema :**
```json
{
  "slots": [
    {
      "start_time": "2026-11-20T09:00:00Z",
      "end_time": "2026-11-20T09:30:00Z",
      "room_or_link": "Salle B12 / Google Meet"
    }
  ]
}
```
* **Response `201 Created` :**
```json
{
  "slots_created": 1,
  "school": "IIT"
}
```

#### 7.2 `GET /responsable/candidates/{id}/brief`
* **Objectif :** Consulter la fiche synthétique 1 page préparée par `VISION 1.6 Registrar` pour l'entretien.
* **Response `200 OK` :**
```json
{
  "candidate_id": "c1a2d3e4-5678-90ab-cdef-1234567890ab",
  "full_name": "Foulen Ben Foulen",
  "school": "IIT",
  "target_program": "Cycle Ingénieur Génie Logiciel",
  "academic_summary": {
    "origin": "ISIMS Sfax (Licence)",
    "official_average": 13.82,
    "qcm_score": "80/100",
    "vlm_confidence": 0.95
  },
  "compatibility_score": 94,
  "brief_pdf_url": "https://minio.smartcampus.nau.tn/briefs/iit/brief_c1a2d3e4.pdf"
}
```

#### 7.3 `POST /responsable/candidates/{id}/decision` *(HITL Exclusif)*
* **Objectif :** **Signature officielle de la décision d'admission.** Seul le `NAU Responsable` est habilité à émettre sur cet endpoint. Tout appel émanant d'un agent automatique ou d'un autre rôle est rejeté en `403 Forbidden`.
* **Headers :** `Authorization: Bearer <token>` (`role: responsable`)
* **Request Schema :**
```json
{
  "decision": "ADMITTED",
  "interviewer_notes": "Très bon niveau scientifique et excellente motivation.",
  "assigned_track": "IIT-ING-GL",
  "signature_token": "sig_rsa_responsable_iit_9841",
  "conditions": "Admission définitive sous réserve de validation du diplôme original de Licence."
}
```
* **Valeurs acceptées :** `ADMITTED`, `WAITLIST`, `REJECTED`
* **Response `200 OK` :**
```json
{
  "candidate_id": "c1a2d3e4-5678-90ab-cdef-1234567890ab",
  "candidate_status": "ADMITTED",
  "signed_by": "Dr. Responsable IIT",
  "signed_at": "2026-11-18T10:45:00Z",
  "outbox_event": "admission_decision_signed",
  "notification_triggered": true
}
```

---

### Module 8 : Catalogue Public & Données de Référence (`/catalog`)

#### 8.1 `GET /catalog/programs`
* **Objectif :** Liste publique des filières et mentions proposées par les écoles NAU (IIT, ISB, extension ISA).
* **Query Params :** `school=iit&degree_level=cycle_ingenieur`
* **Response `200 OK` :**
```json
{
  "school": "IIT",
  "programs": [
    {
      "program_id": "IIT-ING-GL",
      "title": "Cycle Ingénieur en Génie Logiciel",
      "degree": "Diplôme National d'Ingénieur",
      "duration_years": 3,
      "modes": ["Plein temps"],
      "source_document": "Catalogue_IIT_2026.pdf"
    }
  ]
}
```
