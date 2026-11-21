# SmartCampus AI — Extraction VLM des Relevés de Notes Tunisiens (Bac & Licence — Bonus-1 / SA-42)

**Tâche :** `SA-42` / `[Bonus-1]` — Extraire les notes et moyennes depuis une image/PDF de relevé du Bac (et relevé universitaire de Licence pour passerelle Bac+3) en JSON via VLM (`Qwen-VL` / `GPT-4o-mini`) (*GitHub Issue #12*)
**Epic Bonus :** Prétraitement Multimodal & Extraction IA — Story 1 (VLM)
**Composant VISION :** `1.4_Reader`
**Handoff aval :** `Bonus-2` (*Connecter l'extraction JSON au profil candidat PostgreSQL*)

---

## 1. Architecture du Module (`VISION 1.4_Reader`)

Le module d'extraction est exposé via FastAPI (`/api/v1/documents`) et orchestré par `backend/app/services/vlm_transcript_service.py` :

1. **Contrôle d'accès « Give or Keep » & RBAC :**
   - `POST /api/v1/documents/extract-bac-transcript` exige un jeton JWT valide (`role` dans `student`, `rh`, `responsable`, `admin`). Les visiteurs anonymes sont rejetés avec `401 Unauthorized`.
   - `GET /api/v1/documents/vlm/health` expose l'état de configuration du VLM et les formats supportés.
2. **Validation binaire par octets magiques & PDF scannés réels :**
   - Vérification stricte de la taille bornée (`<= 10 Mo`, sinon `413`) et des octets magiques (`%PDF-`, PNG, JPEG, WEBP — sinon `415`).
   - Extraction native des **images raster embarquées dans les PDF scannés** (`pypdf` `page.images`) : un vrai scan PDF tunisien sans couche texte (ex. relevé ISIMS Sfax ou Bac scanné) est automatiquement converti en image redimensionnée (`<= 1600px`) encodée en Base64 Data-URI pour le Vision LLM, sans dépendance OS externe (`poppler`/`tesseract`).
   - Traitement CPU (Pillow / pypdf) isolé via `asyncio.to_thread` pour ne jamais bloquer la boucle asynchrone FastAPI.
3. **Prise en charge de 2 relevés officiels tunisiens (`RELEVE_BAC` et `RELEVE_UNIVERSITAIRE`) :**
   - **Baccalauréat Tunisien (`RELEVE_BAC`, `diploma_stage = "BAC"`) :** format officiel arabe (*بيان أعداد امتحان البكالوريا — وزارة التربية*), mapping canonique des matières arabes, inférence automatique des coefficients officiels du Ministère de l'Éducation (absents du relevé papier), prise en compte automatique des épreuves facultatives (`points > 10/20`, ex. `إسبانية`), décimales à virgule (`09,25`, `11,51`), filtrage des `*****` de la session de contrôle, et normalisation des mentions arabes (`متوسط -> Passable`, `قريب من الحسن -> Assez Bien`, `حسن -> Bien`, `حسن جدا -> Très Bien`).
   - **Relevé Universitaire / Licence Passerelle (`RELEVE_UNIVERSITAIRE`, `diploma_stage = "L1".."L3"|"M1"`) :** adapté aux candidats Bac+3 (ex. Université de Sfax — ISIMS Licence Big Data) candidatant en passerelle 1ère année Cycle Ingénieur IIT, avec extraction des Unités d'Enseignement (`Coef`, `Moy`, `Crédits acquis`), `program_title`, `credits_acquired`, `mention` et `jury_decision`.
4. **Vérification arithmétique & Gouvernance HITL :**
   - Recalcul indépendant de la moyenne pondérée (`compute_tunisian_bac_average`), détection d'écart (`> 0.25` -> `discrepancy_detected = True`, `verification_status = "MISMATCH_RH"`, `needs_manual_review = True`) et marquage de tout champ `< 0.85` dans `low_confidence_fields`.
   - Zéro hallucination hors-ligne : sans `VLM_API_KEY`, une image ou un PDF scanné sans couche texte renvoie `extraction_source = "image_fallback"`, `subjects = []`, `min_confidence = 0.0` et `needs_manual_review = True`.

---

## 2. Contrat JSON `Bonus-1` -> `Bonus-2` (Mapping PostgreSQL)

Le schéma Pydantic `BacTranscriptExtractionResult` (`backend/app/schemas/transcript.py`) s'aligne directement sur les tables SQL `SA-17` (`backend/app/models/users.py`) :

| Champ JSON (`Bonus-1`) | Table & Colonne cible (`Bonus-2`) | Contrainte SQL vérifiée |
| :--- | :--- | :--- |
| `document_type` (`RELEVE_BAC` / `RELEVE_UNIVERSITAIRE`) | `core.document.type` | `IN ('RELEVE_BAC', 'RELEVE_UNIVERSITAIRE', ...)` |
| Payload JSON complet | `core.document.ocr_json` | `JSONB` |
| `min_confidence` | `core.document.min_confidence` | `0.000 <= x <= 1.000` |
| `verification_status` (`PENDING` / `MISMATCH_RH`) | `core.document.verification_status` | `IN ('PENDING', 'CONFIRMED_BY_STUDENT', 'MISMATCH_RH', 'VERIFIED')` |
| `bac_section` / `program_title` | `core.candidate_profile.current_specialty` | Section Bac ou intitulé Licence |
| `general_average` / `calculated_average` | `core.candidate_profile.declared_overall_avg` | `0.00 <= x <= 20.00` |
| `diploma_stage` (`BAC`, `L1`..`L3`, `M1`) | `core.declared_grades.diploma_stage` | `IN ('BAC', 'L1', 'L2', 'L3', ...)` |
| `subjects[i].name` | `core.declared_grades.subject_name` | `VARCHAR(128)` |
| `subjects[i].grade` | `core.declared_grades.grade_value` | `0 <= grade_value <= grade_scale` |
| `subjects[i].coefficient` | `core.declared_grades.coefficient` | `coefficient > 0` |
| `subjects[i].confidence` | `core.declared_grades.confidence_score` | `0.000 <= x <= 1.000` |

---

## 3. Jeux d'Essais Réels Tunisiens (Sfax) 100 % Anonymisés INPDP (Loi 2004-63)

Le dossier `backend/tests/data/sample_bac/` contient **uniquement des documents tunisiens réels de Sfax entièrement anonymisés** (toute donnée personnelle nominative — nom, date de naissance, CIN, numéro d'inscription, code `2D-DOC`, signature manuscrite et métadonnées EXIF — a été définitivement masquée dans les pixels) :

1. **`releve_bac_math_sfax_anonymized.jpg`** — Photographie réelle anonymisée d'un relevé officiel du Baccalauréat tunisien (*الجمهورية التونسية — وزارة التربية — الشعبة: الرياضيات، مكانها: صفاقس، دورة 2021*, 10 matières en arabe dont option `إسبانية`, `المعدل النهائي 11,51`, `الملاحظة متوسط`, vérifié à `172.66 / 15 = 11.51`, `delta = 0.00`).
2. **`releve_bac_math_sfax_anonymized.pdf`** — Version PDF scannée réelle (1 page, image raster embarquée extraite automatiquement par `pypdf` -> VLM) du même relevé du Bac tunisien anonymisé de Sfax.
3. **`releve_licence_l3_isims_sfax_anonymized.pdf`** — Relevé PDF scanné réel anonymisé de **Licence 3ème année — Université de Sfax / ISIMS** (*Licence en Science de l'Informatique : Big Data et Analyse de Données*, 6 UE, `Coef` total `30`, `Stage de fin d'études 15.87`, `Crédits Acquis : 53`, `Moyenne générale : 12.98`, `Mention : ASSEZ BIEN`, `Admis`, vérifié à `389.37 / 30 = 12.98`, `delta = 0.00`) pour les candidatures passerelles Bac+3 vers le Cycle Ingénieur IIT.
