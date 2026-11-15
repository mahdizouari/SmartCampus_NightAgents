# SmartCampus AI — Acteurs, Processus Métiers & Règles HITL (`SA-3`)

> **Tâche :** `[SA-3]` Définir les acteurs et les processus métiers (Issue GitHub `#7` · Epic `SA-1: Architecture & Setup`)  
> **Responsable :** Moataz Triki (`MT` / `@moatazz12`)

---

## 1. Synthèse de la gouvernance (« Human-in-the-Loop »)

> **"VISION talks, JARVIS follows up, humans decide."**  
> Les agents traitent le volume conversationnel et la coordination ; le code déterministe et les règles YAML encadrent strictement leur périmètre d'action ; chaque information sur les formations est sourcée via le corpus RAG ou la base de données, et **seul un responsable humain décide de l'admission officielle**.

### Frontières non négociables (HITL & maîtrise des agents)

| Catégorie | Ce qui est autorisé | Ce qui est strictement interdit |
| :--- | :--- | :--- |
| **Décision d'admission** | **`NAU Responsable`** signe manuellement `ADMITTED \| WAITLIST \| REJECTED`. | Aucun agent (`VISION`, `JARVIS`, `ATLAS`) ni aucun outil ne peut admettre, mettre en liste d'attente, refuser ou promettre une admission. |
| **Données & Règles** | Faits académiques servis uniquement via RAG (`knowledge`) avec citation obligatoire des sources ; frais, prérequis, seuils et échéances lus depuis les tables DB et règles YAML administrées par `Platform Admin`. | Génération libre de frais de scolarité, dates limites, seuils ou contenus de programmes non présents dans le corpus officiel indexé. |
| **Règle d'accès (« Give or Keep »)** | Orientation et échange 100 % anonymes dès le premier message ; authentification possible à tout moment mais exigée uniquement avant le dépôt d'un document officiel, la sauvegarde persistante d'une shortlist ou la préinscription. | Mur de connexion dès l'ouverture du chat, ou stockage d'un relevé de notes officiel sous session anonyme. |
| **Séparation Front / Back** | `VISION` écrit un événement `pre-inscription submitted` dans `core.outbox` ; `JARVIS` réagit uniquement de façon événementielle et sur timers. | `VISION` et `JARVIS` ne s'appellent **jamais** directement. Les utilisateurs humains ne dialoguent jamais directement avec les sous-agents internes (`1.1–1.6`, `2.1–2.3`). |
| **Propriété des dossiers (`One Owner`)** | `candidate.owner` / `rh_ticket.owner` ∈ `{JARVIS, RH:<user>}`. La reprise d'un dossier par `Human RH` suspend immédiatement toute relance automatique. | Envoi parallèle de messages contradictoires par `JARVIS` et par un agent humain. |

---

## 2. Typologie complète des acteurs

Ce modèle unifie les **3 profils d'utilisateurs** du parcours candidat (visiteur sans compte, candidat identifié, responsable), les **rôles fonctionnels d'administration et de pilotage**, ainsi que la formalisation **UML des cas d'utilisation (3 acteurs primaires humains + 2 acteurs secondaires `«agent»`)**.

### 2.1 Acteurs primaires humains (initient les cas d'utilisation)

| Acteur UML | Profils couverts | Rôle & responsabilités | Cas d'utilisation associés |
| :--- | :--- | :--- | :--- |
| **1. `Student`** *(Candidat / futur étudiant)* | **A. Visiteur sans compte** (`ANONYMOUS` → `PROFILING` → `ELIGIBLE \| INELIGIBLE` → `ORIENTED`) | Découvre les formations IIT / ISB *(avec ISA en extension prévue au sein de NAU)* sans friction par texte ou voix (FR, Derja tunisienne, Arabizi) ; pose des questions libres (RAG) ; déclare son niveau et ses notes pour vérifier son éligibilité ou une passerelle/équivalence ; reçoit une shortlist justifiée de 2 à 3 parcours. Mémoire chaude Redis glissante (TTL 7 jours). | `UC01` Ask about programs · `UC02` Get track recommendation · `UC03` Check eligibility (`«include»`) · `UC06` Create an account (`«extend»`) |
| | **B. Candidat identifié** (`AUTHENTICATED` → `PREINSCRIBED` → `QCM_DONE` → `SCHEDULED` → `INTERVIEWED` → `ADMITTED \| WAITLIST \| REJECTED`) | S'authentifie par code OTP (email/téléphone) ou Google OAuth avec fusion automatique de sa session anonyme ; valide son consentement explicite (Loi organique n° 2004-63 / INPDP) ; dépose son relevé officiel pour extraction VLM et pré-remplissage du dossier ; passe le QCM ciblé ; réserve ou replanifie son entretien (max 2 modifications) et suit l'avancement. | `UC04` Submit pre-registration (`«include» UC05`) · `UC05` Log in · `UC07` Take QCM test · `UC08` Manage interview appointment · `UC09` Track application & notifications |
| **2. `Human RH`** *(Admissions Officer)* | **Responsable dossier & exceptions** *(support transversal IIT / ISB / ISA)* | Vérifie et corrige les dossiers en anomalie (`declared ≠ official` après extraction VLM) ; traite toutes les escalades `JARVIS` (absence de réponse à 72h, `NO_SHOW`, 3ème report, réclamations, besoins spécifiques, dérogations) via `rh_ticket` avec contexte résumé en 5 lignes ; approuve les modèles de messages ; peut rendre la main à `JARVIS` en un clic. | `UC10` Process pre-registration dossiers · `UC11` Handle follow-up exceptions · `UC12` View admissions dashboard · `UC05` Log in *(précondition)* |
| **3. `NAU Responsable`** *(par école : IIT · ISB · ISA)* | **Responsable académique et d'entretien** *(rôle consolidé par école, dissociable via RBAC si requis)* | Publie ses créneaux de disponibilité ; consulte le dossier complet, la cohérence de la recommandation, les résultats QCM et le brief 1 page ; conduit l'entretien ; **signe la décision officielle d'admission** (`ADMITTED \| WAITLIST \| REJECTED`). | `UC13` Publish interview availability · `UC14` Evaluate candidate & decide admission · `UC12` View admissions dashboard · `UC05` Log in *(précondition)* |

### 2.2 Rôles secondaires d'exploitation et de pilotage

* **`Direction` :** consulte le tableau de bord global consolidé (`UC12` : entonnoir `visiteur → préinscription → entretien → admission`, répartition des demandes par école, dossiers bloqués ou abandonnés) et, en extension optionnelle, interroge l'assistant analytique **`ATLAS`** (langage naturel → SQL en lecture seule → graphiques).
* **`Platform Admin` :** administre les comptes et permissions RBAC par école, les campagnes d'admission, le corpus documentaire officiel (`knowledge`), les règles d'éligibilité/équivalence YAML, la banque de QCM approuvée, les modèles de notifications et la supervision technique.

### 2.3 Acteurs secondaires système — Architecture Multi-Agents

Les agents interviennent en tant qu'**acteurs secondaires (`«agent»`)** : ils exécutent les traitements déclenchés par les acteurs humains ou par les événements et temporisations autorisés. Chaque composant est explicitement typé (**LLM / VLM / règles / code déterministe**).

| Orchestrateur | Sous-agent / Nœud | Type technique | Rôle exact |
| :--- | :--- | :--- | :--- |
| **`VISION`** *(LangGraph Supervisor · Front-office, Étapes 1–6 + Sign-up)* | `1.1 Listener` | **LLM** (+ Whisper STT, LID, normaliseur Arabizi) | Transcrit l'audio, normalise Derja/Arabizi, détecte l'intention et la langue. |
| | `1.2 Librarian` | **LLM** (+ pgvector BM25 + vecteurs) | Répond en continu sur le corpus officiel IIT / ISB / ISA avec citations obligatoires des sources. |
| | `1.3 Gatekeeper` | **LLM + Règles YAML** | Extrait les faits déclarés (`candidate_profile`) et évalue l'éligibilité, les prérequis et les passerelles/équivalences de manière déterministe. |
| | `1.4 Reader` | **VLM** (+ OpenCV) | Extrait les matières, notes et moyennes du relevé officiel après authentification, avec score de confiance par champ. |
| | `1.5 Advisor` | **Code + LLM** | Calcule `score_compatibilite()` (critères académiques et intérêts uniquement, sans biais) et justifie 2 à 3 parcours. |
| | `1.6 Registrar` | **LLM** | Pré-remplit le formulaire cible, génère le QCM (5–10 questions) depuis la banque approuvée, rédige le brief 1 page et émet l'événement `outbox`. |
| **`JARVIS`** *(Event / Timer Workers · Back-office, Étape 7)* | `2.1 Scheduler` | **Code déterministe** | Gère les créneaux (`FREE / HELD / BOOKED`), interdit tout double-booking (`unique(responsable_id, start_time)` + `SELECT ... FOR UPDATE`), gère automatiquement la liste d'attente (blocage temporaire 15 min aux 3 candidats suivants). |
| | `2.2 Messenger` | **LLM** (borné par templates RH) | Personnalise les relances (T-24h, 24h, 48h), interprète les réponses (confirmer / déplacer / annuler) et notifie la décision humaine signée. |
| | `2.3 Fixer` | **Règles + LLM** | Libère les créneaux annulés, applique le plafond `reschedules <= 2` et ouvre les tickets `rh_ticket` (résumé 5 lignes + historique chronologique). |
| **Transversal** | **Guardrail Node** | **Règles + LLM** | Contrôle chaque sortie : présence de citation pour tout fait académique, aucune promesse d'admission, aucun frais ou délai inventé, protection contre l'injection de prompt et masquage PII dans les journaux. |
| **Optionnel (*stretch*)** | **`ATLAS`** | **LLM** | Traduit les questions de la `Direction` en requêtes SQL en lecture seule sur les vues statistiques. |

---

## 3. Processus métiers détaillés (8 étapes + inscription parallèle)

Le pipeline à **8 étapes séquentielles et inscription parallèle** couvre l'ensemble du cycle de vie, de la première prise de contact anonyme jusqu'à la notification de la décision humaine, en structurant les **4 processus métiers clés** :

```mermaid
flowchart LR
    subgraph P1["Phase 1 - DISCOVER (Visiteur sans compte · Redis TTL 7j)"]
        S1["1. Talk (Texte / Audio FR - Derja - Arabizi)"] --> S2["2. Ask & Build Profile (RAG + Faits déclarés)"]
        S2 --> S3["3. Check Eligibility & Equivalence (Règles YAML)"]
        S3 --> S4["4. Recommend 2-3 Tracks (Shortlist justifiée)"]
    end

    SP["Parallel Sign-Up 'Give or Keep' (OTP / OAuth + Consentement INPDP + Fusion session)"]
    S1 -. "Optionnel" .-> SP
    S4 -- "Obligatoire avant dépôt relevé" --> SP

    subgraph P2["Phase 2 - ENROL (Candidat identifié · PostgreSQL + MinIO)"]
        S5["5. Préinscription (Upload relevé VLM + Formulaire + Routage core/école)"] --> S6["6. Short QCM (5-10 q.) + Brief 1 page"]
    end

    SP --> S5

    subgraph P3["Phase 3 - FOLLOW UP (JARVIS & Human RH)"]
        S7["7. Book, Remind & Reschedule (max 2) · Escalade RH si exception"]
    end

    S6 -- "Outbox: pre-inscription submitted" --> S7

    subgraph P4["Phase 4 - DECIDE (Validation HITL)"]
        S8["8. Entretien & Décision signée (NAU Responsable) -> Notification"]
    end

    S7 --> S8
```

### 3.1 Processus 1 : Demandes d'équivalence, d'éligibilité & orientation (`Étapes 1–4`)

* **Acteurs :** `Student` *(visiteur sans compte)*, `VISION` (`1.1 Listener`, `1.2 Librarian`, `1.3 Gatekeeper`, `1.5 Advisor`).
* **Déroulement :**
  1. **Accueil multimodal (Étape 1) :** le visiteur saisit un message texte ou envoie un audio (transcrit via Whisper STT). `1.1 Listener` identifie la langue (FR / Derja / Arabizi), normalise l'Arabizi et détecte l'intention.
  2. **Information programmes & passerelles en continu (Étape 2) :** à tout instant, `1.2 Librarian` interroge `knowledge` (pgvector) pour renseigner le visiteur sur les filières (IIT Licence / Prépa / Ingénierie, ISB, extension ISA) et les passerelles/équivalences, avec **citation exacte** de la source (ou refus explicite avec contact humain si la demande sort du corpus). Les éléments scolaires déclarés (diplôme actuel : Bac / Licence / Prépa, moyenne déclarée, spécialité, intérêts) alimentent progressivement `candidate_profile`.
  3. **Traitement des demandes d'éligibilité & équivalence (Étape 3) :** `1.3 Gatekeeper` applique le moteur de règles YAML (alimenté par les grilles officielles configurées par l'administration) pour évaluer l'accès direct ou par équivalence/passerelle :
     * **Éligible (`ELIGIBLE`)** → poursuite vers le calcul de recommandation.
     * **Cas limite / équivalence particulière** → marquage `"à confirmer par le Responsable"` afin de garantir une vérification humaine sans rejet abusif ni promesse d'admission.
     * **Non éligible (`INELIGIBLE`)** → arrêt précoce avant tout traitement coûteux (VLM/QCM) et présentation des alternatives ou passerelles existantes.
  4. **Recommandation justifiée (Étape 4) :** `1.5 Advisor` calcule `score_compatibilite()` (exclusivement à partir des critères académiques et intérêts déclarés) et restitue **2 à 3 parcours côte à côte** avec décomposition du score, points forts, prérequis et références sourcées. Le visiteur conserve son libre choix (`ORIENTED`).

### 3.2 Processus 2 : Authentification (« Give or Keep ») & Préinscription (`Sign-up + Étapes 5–6`)

* **Acteurs :** `Student` *(visiteur → candidat identifié)*, `VISION` (`1.4 Reader`, `1.6 Registrar`), `Human RH` *(si anomalie documentaire)*.
* **Déroulement :**
  1. **Authentification progressive (« Give or Keep ») :** facultative durant les étapes 1 à 4 (*« Sauvegarder ma progression »* ou *« Recevoir ma shortlist par email »*), **obligatoire juste avant le dépôt du relevé de notes** (`UC04 «include» UC05`). Inscription rapide sans formulaire lourd : code OTP (email/téléphone) ou Google OAuth + **validation d'un consentement explicite** journalisé dans `consent` (Loi organique n° 2004-63 / INPDP).
  2. **Fusion de session :** le profil déclaré et la shortlist stockés dans Redis sont fusionnés sans perte dans PostgreSQL (`core.candidate`, `core.candidate_profile`, `core.shortlist`) ; le candidat acquiert le statut `AUTHENTICATED`.
  3. **Dépôt relevé & extraction VLM (Étape 5) :** le relevé officiel est stocké chiffré dans MinIO (`core.document`). `1.4 Reader` (VLM + OpenCV) extrait les matières, notes et moyennes avec un indice de confiance par champ. `1.6 Registrar` pré-remplit le formulaire spécifique à l'école visée et met en évidence les champs incertains pour validation manuelle par le candidat.
  4. **Contrôle de cohérence & routage transactionnel :**
     * En cas d'écart significatif persistant entre notes déclarées et relevé officiel après relecture du candidat → ouverture d'un ticket `rh_ticket` pour contrôle par **`Human RH`** (`UC10`).
     * Lors de la validation finale → écriture **atomique (transaction unique)** dans `core` et dans le schéma de l'école cible (`iit`, `isb` ou `isa`) sous isolation RLS, passage à `PREINSCRIBED` et émission d'un événement `pre-inscription submitted` dans `core.outbox`.
  5. **QCM ciblé & Brief d'entretien (Étape 6) :** `1.6 Registrar` sélectionne **5 à 10 questions** dans la banque approuvée par `Platform Admin` / `NAU Responsable`, évalue automatiquement les réponses et génère un **brief synthétique d'1 page** pour l'évaluateur (`QCM_DONE`).

### 3.3 Processus 3 : Prise de rendez-vous, Relances & Escalade RH (`Étape 7`)

* **Acteurs :** `Student` *(candidat identifié)*, `JARVIS` (`2.1 Scheduler`, `2.2 Messenger`, `2.3 Fixer`), `Human RH`, `NAU Responsable` *(publication des créneaux `UC13`)*.
* **Machine à états des rendez-vous :**

```text
AWAITING_SLOT --> INVITED --> BOOKED --> REMINDED --> ATTENDED --> (Étape 8 : Décision)
                     |          |            |
          (72h sans  |          | (cancel    | (absent jour J)
           réponse)  |          |  ou report)|
                     |          v            v
                     |      CANCELLED     NO_SHOW ----+
                     |    (slot libéré ;              |
                     |     réallocation <= 2)         |
                     |          |                     |
                     |   (si report > 2)              |
                     v          v                     v
                 ESCALATED <--------------------------+  (owner = Human RH)
                     |
          (clôturé)  v
                 WITHDRAWN
```

* **Matrice d'escalade et bornes d'autorité (`JARVIS` vs `Human RH`) :**

| Situation | Action autorisée de `JARVIS` | Escalade vers `Human RH` (`rh_ticket` + arrêt auto) |
| :--- | :--- | :--- |
| Préinscription démarrée non soumise | Relances à 48h et 7 jours | Jamais (nettoyage automatique en fin de campagne) |
| Invitation sans réponse | Relances à 24h et 48h | **À 72h sans réponse** → statut `DORMANT` + ticket RH |
| Demande de report ou d'annulation | Propose d'autres créneaux ; libère le créneau annulé (blocage temporaire 15 min pour les 3 candidats suivants en liste d'attente) | **À la 3ème modification** (`reschedules > 2`) |
| Absence le jour de l'entretien | Enregistre `NO_SHOW` | **Immédiat** — reprise manuelle par appel humain |
| Annulation par `NAU Responsable` | Transmet les excuses et propose des créneaux prioritaires | **Si aucun créneau disponible sous 7 jours** |
| Anomalie relevé / réclamation / exception frais ou délai | Accuse réception de façon neutre, sans aucun engagement | **Immédiat** (discordance confirmée ou toute demande d'exception) |

* **Garantie d'idempotence :** clé unique `appointment_id + message_type` couplée à un balayage périodique toutes les 10 minutes afin d'éviter toute relance manquée ou envoyée en double.

### 3.4 Processus 4 : Entretien & Décision finale HITL (`Étape 8`)

* **Acteurs :** `NAU Responsable` *(décideur unique)*, `Student` *(destinataire)*, `JARVIS` *(messager uniquement après validation humaine)*.
* **Déroulement :**
  1. En amont de l'entretien, `NAU Responsable` consulte depuis son portail le **brief 1 page**, les scores QCM, l'historique d'orientation et les éventuelles alertes d'équivalence ou d'anomalie (`UC14`).
  2. Il mène l'entretien (`INTERVIEWED`) puis **saisit et signe manuellement la décision officielle** accompagnée de ses observations : **`ADMITTED`**, **`WAITLIST`** ou **`REJECTED`**.
  3. Dès enregistrement de cette signature humaine, `JARVIS` adresse la notification validée (email et portail candidat), archive l'historique du dossier et applique la politique de rétention des données.

---

## 4. Machine à états complète du candidat

Une colonne unique `candidate.status` assure le suivi cohérent de l'avancement du dossier sur l'ensemble des portails et workflows :

```text
ANONYMOUS --> PROFILING --> ELIGIBLE | INELIGIBLE --> ORIENTED --> AUTHENTICATED
   --> PREINSCRIBED --> QCM_DONE --> SCHEDULED --> INTERVIEWED --> ADMITTED | WAITLIST | REJECTED

États transversaux : DORMANT · ESCALATED · WITHDRAWN
```

---

## 5. Paramètres d'intégration et règles de configuration

* **Référentiel académique et règles d'éligibilité :** les catalogues détaillés de formations, les grilles et seuils d'équivalence/éligibilité (YAML), les frais de scolarité ainsi que les dates de campagne sont administrés par `Platform Admin` à partir des documents officiels validés ; aucune valeur variable n'est figée dans le code ou les prompts des agents.
* **Extension multi-écoles (ISA) :** le socle cible prioritairement **IIT** et **ISB**, tout en prévoyant dès l'architecture le routage transactionnel, le schéma PostgreSQL dédié (`isa`) et l'isolation RLS pour l'activation d'**ISA** au sein de l'écosystème NAU.
* **Granularité RBAC du rôle Responsable :** le rôle `NAU Responsable` consolide la gestion des disponibilités, la conduite de l'entretien et la décision finale par établissement ; la séparation entre `Responsable académique` et `Responsable entretien` reste activable par simple configuration des permissions si l'organisation le requiert.
