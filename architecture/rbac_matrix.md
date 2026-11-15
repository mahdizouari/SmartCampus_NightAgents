# SmartCampus AI — Matrice RBAC & Règles d'Accès Sécurisées (`SA-5.1`)

> **Tâche :** `[SA-5]` Concevoir les API et les règles d'accès — Volet Sécurité & RBAC  
> **Dépendances :** `SA-3` (Acteurs & Workflows), `SA-4` (Architecture globale 5 couches)  
> **Règle d'or HITL :** *"VISION talks, JARVIS follows up, humans decide."*

---

## 1. Typologie des Acteurs & Tokens de Sécurité

Le système encadre quatre acteurs opérationnels majeurs ainsi que les rôles de supervision :

| Rôle RBAC | Acteur métier | Mécanisme d'authentification | Claims JWT / Headers | Durée de vie & Session | Périmètre & Isolation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`ANONYMOUS_GUEST`** | Visiteur sans compte (futur étudiant) | En-tête obligatoire :<br/>`X-Session-ID: <UUIDv4>` | N/A (Session clé-valeur Redis) | Redis TTL 7 jours glissant (rafraîchi à chaque requête) | Isolation stricte en mémoire Redis. Découverte de l'offre académique, chat multilingue, calculs d'éligibilité déclaratifs. **Aucune donnée personnelle stockée en DB.** |
| **`AUTHENTICATED_STUDENT`** | Candidat identifié | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `student`<br/>`candidate_id`: `<uuid>` | Access Token : 15 min<br/>Refresh Token : 7 jours | Row-Level Security (RLS) PostgreSQL : accès limité à son propre dossier (`WHERE candidate_id = current_user.candidate_id`). Téléversement de relevé MinIO, passage QCM, réservation d'entretien (max 2 reports). |
| **`HUMAN_RH`** | Responsable Admissions & Exceptions | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `rh`<br/>`department`: `admissions` | Access Token : 30 min (SSO / OTP sécurisé) | Accès transversal multi-écoles (IIT, ISB, ISA). Traitement des tickets d'escalade `rh_ticket`, arbitrage des discordances de notes VLM (`declared ≠ official`), prise en main de dossier (`owner = RH:<user>`). **Interdiction formelle de signer une admission.** |
| **`NAU_RESPONSABLE`** | Responsable Académique & Évaluateur | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `responsable`<br/>`school`: `iit \| isb \| isa` | Access Token : 30 min (SSO sécurisé) | Partitionné par école via RLS. Publication des créneaux `FREE`, consultation de la fiche synthétique 1 page + résultats QCM. **Signataire HITL exclusif de la décision officielle (`ADMITTED \| WAITLIST \| REJECTED`).** |
| *`DIRECTION`* | Direction Générale NAU / Écoles | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `direction` | Access Token : 30 min | Lecture seule sur les tableaux de bord d'entonnoir global (`UC12`) et requêtes analytiques via l'assistant `ATLAS` (SQL en lecture seule). |
| *`PLATFORM_ADMIN`* | Administrateur Technique | En-tête :<br/>`Authorization: Bearer <JWT>` | `sub`: `<user_id>`<br/>`role`: `admin` | Access Token : 15 min + MFA obligatoire | Gestion des comptes RBAC, grilles de règles d'éligibilité YAML, gestion du corpus RAG `knowledge`, banque de questions QCM. |

---

## 2. Principes Fondamentaux de Sécurité & Frontières HITL

### 2.1 Règle d'Accès Progressif (« Give or Keep »)
1. **Zéro barrière initiale :** Le visiteur bénéficie d'une orientation complète (audio/texte FR, Derja, Arabizi), interroge la base de connaissances officielle et teste son éligibilité sans créer de compte ni renseigner son identité.
2. **Déclencheurs d'authentification obligatoire :** L'authentification (OTP ou OAuth Google) est exigée immédiatement et impérativement avant :
   * Le téléversement d'un document officiel (relevé de notes dans MinIO).
   * La sauvegarde persistante ou l'envoi par email d'une shortlist de formations.
   * La validation d'une préinscription et la prise de rendez-vous d'entretien.
3. **Fusion de session transparente (`/auth/merge-session`) :**
   * Lors du passage de visiteur à candidat, le frontend transmet le jalon `X-Session-ID` conjointement avec le token Bearer.
   * Le backend copie atomiquement les faits déclarés et l'historique conversationnel depuis Redis vers PostgreSQL (`core.candidate`, `core.candidate_profile`, `core.shortlist`), et bascule le statut du candidat de `ANONYMOUS` vers `AUTHENTICATED`.

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

| Domaine / Ressource | `ANONYMOUS_GUEST` | `AUTHENTICATED_STUDENT` | `HUMAN_RH` | `NAU_RESPONSABLE` | `DIRECTION` | `PLATFORM_ADMIN` |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Sessions & Auth** (`/auth/*`) | C (guest, otp) | C (login, consent), R (me) | C (login), R (me) | C (login), R (me) | C (login), R (me) | C, R, U, D (comptes) |
| **Catalogue & RAG** (`/catalog/*`) | R (public) | R (public) | R (public) | R (public) | R (public) | C, R, U, D (gestion) |
| **Chat VISION** (`/chat/*`) | C, R (Redis hot) | C, R (Own DB + Redis) | — | — | — | — |
| **Éligibilité / YAML** (`/orientation/*`)| C (simulation) | C, R (Own shortlist) | R (audit) | R (audit) | R (stats) | C, R, U (règles) |
| **Relevés & VLM** (`/documents/*`) | — | C, R (Own) | R (tous), U (notes) | R (School via brief) | — | R (audit) |
| **Dossiers & QCM** (`/enrolment/*`) | — | C, R, U (Own) | R (tous) | R (School) | — | R (audit) |
| **Créneaux Slots** (`/scheduling/slots`)| R (public) | R (disponibles) | R, C, U (dépannage) | C, R, U, D (Own School)| R | C, R, U, D |
| **Rendez-vous** (`/scheduling/appointments`)| — | C, R (Own), U (max 2) | R (tous), U (manuel) | R (Own School), U (présence)| R | R, U |
| **Tickets RH** (`/rh/tickets/*`) | — | — | C, R, U (assign/résoudre)| — | R (stats) | R (supervision) |
| **Brief d'entretien** (`/responsable/briefs/*`)| — | — | R (sur ticket RH) | R (Own School) | — | R (audit) |
| **Décision Admission** (`/responsable/decisions`)| — *(interdit)* | — *(interdit)* | — *(interdit)* | **C, R (Own School exclusif)**| R (lecture) | R (audit) |
| **Tableaux de bord & ATLAS** (`/analytics/*`)| — | — | R (opérationnel RH) | R (statistiques école) | **R (entonnoir consolidé)**| R (métriques infra) |

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
