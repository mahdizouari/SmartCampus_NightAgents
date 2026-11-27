# SmartCampus AI — Matrice RBAC & Règles d'Accès Sécurisées (`SA-5.1`)

> **Tâche :** `[SA-5]` Concevoir les API et les règles d'accès — Volet Sécurité & RBAC  
> **Dépendances :** `SA-3` (Acteurs & Workflows), `SA-4` (Architecture globale 5 couches)  
> **Règle d'or HITL :** *"VISION talks, JARVIS follows up, humans decide."*

---

## 1. Typologie des Acteurs & Tokens de Sécurité

Le modèle cible simplifié repose sur **trois stades d'acteurs canoniques (`ActorStage`)** : **`VISITEUR`** (avant authentification), **`CANDIDAT`** (après authentification + fusion automatique de session), et **`RESPONSABLE`** (suivi dossiers, décisions HITL et mise à jour BD/RAG cloisonnée par école), complétés en support opérationnel par `HUMAN_RH`. Les rôles historiques `DIRECTION` et `PLATFORM_ADMIN` sont retirés du périmètre utilisateur cible (seules les routes techniques de registre racine d'écoles et de compatibilité SQL conservent la distinction interne).

| Stade (`ActorStage`) / Rôle RBAC | Acteur métier | Mécanisme d'authentification | Claims JWT / Headers | Durée de vie & Session | Périmètre & Isolation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`VISITEUR`** (`ANONYMOUS_GUEST` · `guest` / `visiteur`) | Visiteur sans compte (lycéen, étudiant ou parent/proche) | Cookie signé `smartcampus_guest_session` ou en-tête `X-Session-ID: <UUIDv4>` | N/A (Session clé-valeur Redis) | Redis TTL 7 jours glissant (rafraîchi à chaque requête) | Isolation stricte en mémoire Redis. Découverte de l'offre académique, chat multilingue, calculs d'éligibilité déclaratifs. **Aucune donnée personnelle stockée en DB.** |
| **`CANDIDAT`** (`AUTHENTICATED_STUDENT` · `candidat` / `student`) | Candidat identifié | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `candidat` ou `student` *(alias équivalents)*<br/>`candidate_id`: `<uuid>` | Access Token : 60 min<br/>Refresh Token : 7 jours | Row-Level Security (RLS) PostgreSQL : accès limité à son propre dossier (`WHERE candidate_id = current_user.candidate_id`). Téléversement de relevé VLM, passage QCM, réservation d'entretien (max 2 reports). |
| **`RESPONSABLE`** (`NAU_RESPONSABLE` · `responsable`) | Responsable Académique, Gestion BD/RAG & Évaluateur | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `responsable`<br/>`school_scope`: `iit \| isb \| isa` | Access Token : 60 min | Partitionné par école via `school_scope` + RLS. CRUD catalogue (`programs`, `prerequisites`, `fees`, `campaigns`) et corpus RAG (`knowledge/documents`) pour son école, publication des créneaux `FREE`, suivi des dossiers/briefs 1 page, et **signataire HITL exclusif de la décision officielle (`ADMITTED \| WAITLIST \| REJECTED`).** |
| *`HUMAN_RH`* (`rh`, support exceptions) | Support Admissions & Exceptions | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `rh` | Access Token : 60 min | Accès transversal multi-écoles (IIT, ISB, ISA) pour les tickets `rh_ticket` et l'arbitrage des discordances VLM (`declared != official`). **Interdiction formelle de signer une admission.** |
| *`DIRECTION` / `PLATFORM_ADMIN`* *(retirés du périmètre cible)* | Rôles techniques historiques | En-tête :<br/>`Authorization: Bearer <JWT>` | `role`: `direction` / `admin` | Compatibilité migrations | `DIRECTION` n'a plus de droits d'écriture sur `/api/v1/knowledge/documents` (lecture seule résiduelle). `admin` reste limité aux opérations techniques de registre racine (`/api/v1/catalog/schools`). |

---

## 2. Principes Fondamentaux de Sécurité & Frontières HITL

### 2.1 Règle d'Accès Progressif (« Give or Keep »)
1. **Zéro barrière initiale :** Le visiteur bénéficie d'une orientation complète (audio/texte FR, Derja, Arabizi), interroge la base de connaissances officielle et teste son éligibilité sans créer de compte ni renseigner son identité.
2. **Déclencheurs d'authentification obligatoire :** L'authentification (OTP ou OAuth Google) est exigée immédiatement et impérativement avant :
   * Le téléversement d'un document officiel (relevé de notes dans MinIO).
   * La sauvegarde persistante ou l'envoi par email d'une shortlist de formations.
   * La validation d'une préinscription et la prise de rendez-vous d'entretien.
3. **Fusion de session automatique (`Visiteur -> Candidat`) :**
   * Lors de `POST /api/v1/auth/otp/verify` ou `POST /api/v1/auth/oauth/google` (ainsi que via l'endpoint explicite `POST /api/v1/auth/merge-session`), le backend détecte la session invité depuis `anonymous_session_id`, le cookie signé `smartcampus_guest_session` ou l'en-tête `X-Session-ID`.
   * Le backend copie atomiquement les faits déclarés et l'historique conversationnel depuis Redis vers PostgreSQL (`core.candidate`, `core.candidate_profile`, `core.shortlist`), efface le cookie invité, bascule le statut de `ANONYMOUS` vers `AUTHENTICATED`, et retourne `actor_stage = "CANDIDAT"`, `session_merged: bool` et `turns_merged: int`.

### 2.2 Verrou d'Admission HITL (Human-in-the-Loop)
* **Principe absolu :** Aucun agent (`VISION`, `JARVIS`, `ATLAS`), aucun service automatisé ni aucun profil `HUMAN_RH` ne détient les droits d'écriture sur la table des décisions d'admission.
* **Exclusivité du rôle `NAU_RESPONSABLE` :** Seul un utilisateur détenant le rôle `NAU_RESPONSABLE` et rattaché à l'école cible (`iit`, `isb` ou `isa`) peut émettre une signature sur l'endpoint `POST /responsable/candidates/{candidate_id}/decision`. Toute tentative externe est bloquée avec un code HTTP `403 Forbidden` et journalisée dans `core.audit`.

### 2.3 Règle du Propriétaire Unique (« One Owner »)
* À tout instant, la gestion d'un dossier obéit à la contrainte : `candidate.owner` / `rh_ticket.owner` $\in$ `{JARVIS, RH:<user_id>}`.
* Dès qu'un agent `HUMAN_RH` prend en charge un ticket (`PATCH /rh/tickets/{id}/claim`), `owner` passe à `RH:<user_id>`, ce qui **suspend automatiquement et immédiatement l'ensemble des relances programmées par JARVIS** pour ce candidat.

---

## 3. Matrice Globale des Droits d'Accès (CRUD)

**Légende des permissions :**
* **C** : Create (Création)
* **R** : Read (Lecture)
* **U** : Update (Modification)
* **D** : Delete (Suppression)
* **Own** : Limité strictement aux enregistrements associés à l'identité du demandeur (`candidate_id = current_user.id` ou `owner = current_user.id`)
* **School** : Limité au schéma et aux données de l'établissement auquel le responsable est affecté (`iit`, `isb` ou `isa`)
* **—** : Accès formellement interdit (HTTP `403 Forbidden` ou `401 Unauthorized`)

| Domaine / Ressource | `VISITEUR` (`guest` / `visiteur`) | `CANDIDAT` (`candidat` / `student`) | `RESPONSABLE` (`responsable`) | `HUMAN_RH` (`rh`, support) | *Legacy `admin` (registre racine)* |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Sessions & Auth** (`/auth/*`) | C (guest, otp) | C (login, auto-merge, consent), R (me) | C (login), R (me) | C (login), R (me) | C, R, U, D (technique) |
| **Catalogue — Programmes, Prérequis, Frais, Campagnes** (`/catalog/*`) | R (public) | R (public) | **C, R, U, D (Own School)** | R (public) | C, R, U, D (toutes écoles + `/schools`) |
| **Corpus RAG & Documents** (`/knowledge/documents/*`) | R (publiés) | R (publiés) | **C, R, U, D (Own School)** | R (diagnostic) | C, R, U, D |
| **Chat VISION** (`/chat/*`) | C, R (Redis hot) | C, R (Own DB + Redis) | R (`/evaluate-dialect`) | — | R (`/evaluate-dialect`) |
| **Éligibilité / YAML** (`/orientation/*`) | C (simulation) | C, R (Own shortlist) | R, U (Own School) | R (audit) | C, R, U |
| **Relevés & VLM** (`/documents/*`) | — | C, R (Own) | R (Own School via brief) | R (tous), U (anomalies) | R (audit) |
| **Dossiers & QCM** (`/enrolment/*`) | — | C, R, U (Own) | R (Own School) | R (tous) | R (audit) |
| **Créneaux Slots** (`/scheduling/slots`) | R (public) | R (disponibles) | **C, R, U, D (Own School)** | R, C, U (dépannage) | C, R, U, D |
| **Rendez-vous** (`/scheduling/appointments`) | — | C, R (Own), U (max 2) | R (Own School), U (présence) | R (tous), U (manuel) | R, U |
| **Tickets RH** (`/rh/tickets/*`) | — | — | R (Own School) | C, R, U (assign/résoudre) | R (supervision) |
| **Brief d'entretien** (`/responsable/briefs/*`) | — | — | R (Own School) | R (sur ticket RH) | R (audit) |
| **Décision Admission** (`/responsable/decisions`) | — *(interdit)* | — *(interdit)* | **C, R (Own School exclusif)** | — *(interdit)* | R (audit) |
| **Tableaux de bord** (`/analytics/*`) | — | — | **R (statistiques & entonnoir école)** | R (opérationnel RH) | R (métriques infra) |

> **Retrait des droits d'écriture `direction` :** le rôle historique `direction` reçoit `403 Forbidden` sur toutes les mutations `/api/v1/knowledge/documents` et `/api/v1/catalog/*`.

---

## 4. Politiques PostgreSQL Row-Level Security (RLS)

Afin de garantir l'isolation logique définie dans `SA-4`, les schémas PostgreSQL appliquent des politiques RLS strictes :

```sql
-- 1. Isolation du profil candidat dans le schéma core
ALTER TABLE core.candidate ENABLE ROW LEVEL SECURITY;

CREATE POLICY student_own_candidate ON core.candidate
  FOR ALL
  TO authenticated_student
  USING (candidate_id = current_setting('request.jwt.claim.candidate_id', true)::uuid);

CREATE POLICY rh_all_candidates ON core.candidate
  FOR SELECT
  TO human_rh
  USING (true);

-- 2. Isolation multi-écoles (Exemple : IIT)
ALTER TABLE iit.dossiers ENABLE ROW LEVEL SECURITY;

CREATE POLICY student_own_iit_dossier ON iit.dossiers
  FOR ALL
  TO authenticated_student
  USING (candidate_id = current_setting('request.jwt.claim.candidate_id', true)::uuid);

CREATE POLICY responsable_iit_dossiers ON iit.dossiers
  FOR SELECT
  TO nau_responsable
  USING (current_setting('request.jwt.claim.school', true) = 'iit');

-- 3. Verrou strict sur la signature des décisions d'admission
ALTER TABLE iit.decisions ENABLE ROW LEVEL SECURITY;

CREATE POLICY responsable_iit_insert_decision ON iit.decisions
  FOR INSERT
  TO nau_responsable
  WITH CHECK (
    current_setting('request.jwt.claim.school', true) = 'iit'
    AND current_setting('request.jwt.claim.role', true) = 'responsable'
  );
```

---

## 5. Politiques de Limitation de Débit (Rate Limiting HTTP) & Plafonds Métiers

### 5.1 Quotas Techniques de Débit HTTP (FastAPI / Redis)

Afin de protéger les ressources d'inférence coûteuses (LLM, Whisper STT, VLM OCR), des plafonds stricts en nombre de requêtes HTTP sont appliqués :

| Profil d'acteur | Quota global API | Quota Chat LLM / STT (speech to text) (`/chat/message`) | Quota Upload VLM (`/documents/*`) |
| :--- | :--- | :--- | :--- |
| **`ANONYMOUS_GUEST`** | 30 req / min par IP | 15 req / min par session | 0 req / min *(accès interdit avant auth)* |
| **`AUTHENTICATED_STUDENT`**| 120 req / min par compte | 30 req / min par compte | 5 req / heure par compte |
| **`HUMAN_RH`** | 300 req / min par compte | N/A (portail de gestion) | 30 req / heure par compte |
| **`NAU_RESPONSABLE`** | 300 req / min par compte | N/A (portail d'évaluation) | 30 req / heure par compte |

### 5.2 Plafonds & Règles Métiers Associées (Business Logic)
Distincts des quotas réseau, ces plafonds sont garantis par le code métier :
* **Visiteur anonyme :** Aucune réservation de créneau autorisée.
* **Candidat authentifié :** 1 seul rendez-vous actif et un plafond strict de **2 reports de date maximum** (au 3ᵉ report $\rightarrow$ blocage et escalade `rh_ticket`).
* **Responsable / RH :** Pas de restriction sur la publication de créneaux ou le calage manuel de rendez-vous en exception.

### 5.3 En-têtes de Réponse Rate Limit (IETF Draft)
* `RateLimit-Limit` : Nombre maximum de requêtes autorisées sur la fenêtre.
* `RateLimit-Remaining` : Nombre de requêtes restantes sur la fenêtre courante.
* `RateLimit-Reset` : Nombre de secondes avant réinitialisation du quota.
* En cas de dépassement : code HTTP `429 Too Many Requests` avec en-tête `Retry-After: <secondes>`.
