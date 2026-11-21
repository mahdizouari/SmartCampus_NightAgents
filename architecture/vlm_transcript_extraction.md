# SmartCampus AI — Extraction VLM des Relevés de Notes du Baccalauréat (Bonus-1 / SA-42)

**Tâche :** `SA-42` / `[Bonus-1]` — Extraire les notes et moyennes depuis une image/PDF de relevé du Bac en JSON via VLM (`Qwen-VL` / `GPT-4o-mini`) (*GitHub Issue #12*)
**Epic Bonus :** Prétraitement Multimodal & Extraction IA — Story 1 (VLM)
**Composant VISION :** `1.4_Reader`
**Handoff aval :** `Bonus-2` (*Connecter l'extraction JSON au profil candidat PostgreSQL*)

---

## 1. Architecture du Module (`VISION 1.4_Reader`)

Le module d'extraction est exposé via FastAPI (`/api/v1/documents`) et orchestré par `backend/app/services/vlm_transcript_service.py` :

1. **Contrôle d'accès « Give or Keep » & RBAC :**
   - `POST /api/v1/documents/extract-bac-transcript` exige un jeton JWT valide (`role` dans `student`, `rh`, `responsable`, `admin`). Les visiteurs anonymes sont rejetés avec `401 Unauthorized`.
   - `GET /api/v1/documents/vlm/health` expose l'état de configuration du VLM et les formats supportés.
2. **Validation binaire par octets magiques (sécurité upload) :**
   - Vérification stricte de la taille (`<= 10 Mo`, sinon `413`) et des octets magiques (`%PDF-`, PNG, JPEG, WEBP — sinon `415`), sans jamais se fier uniquement à l'extension du fichier.
3. **Chaîne d'extraction multimodale :**
   - **Mode VLM Live (`extraction_source = "vlm_live"`) :** Appelle l'API Vision OpenAI-compatible configurée (`qwen/qwen3.8-27b` sur Groq par défaut selon `architecture/llm_models.md`, ou `gpt-4o-mini` / `Qwen2.5-VL` via OpenRouter/OpenAI) avec encodage Base64 Data-URI (redimensionné à `<= 1600px` via Pillow) ou texte extrait du PDF (`pypdf`) et `response_format={"type": "json_object"}`.
   - **Mode Parser PDF Local (`extraction_source = "pdf_text_parser"`) :** Lorsque `VLM_API_KEY` et `LLM_API_KEY` ne sont pas configurés, les relevés PDF numériques tunisiens sont analysés en local via `pypdf` avec contrôle des coefficients officiels du Ministère de l'Éducation.
   - **Repli Image Honnête (`extraction_source = "image_fallback"`) :** Sans clé VLM distante sur une image raster, le service n'invente jamais de notes : il retourne `subjects = []`, `min_confidence = 0.0`, `needs_manual_review = True` et un avertissement explicite.
4. **Vérification arithmétique & Gouvernance HITL :**
   - Recalcul indépendant de la moyenne générale pondérée tunisienne (`compute_tunisian_bac_average`), y compris la règle des matières optionnelles (points au-dessus de `10/20`).
   - Détection automatique des écarts (`|general_average - calculated_average| > 0.25` ou écart avec `declared_average`) -> `discrepancy_detected = True`, `verification_status = "MISMATCH_RH"`, `needs_manual_review = True`.
   - Toute matière ou métadonnée dont la confiance est `< VLM_CONFIDENCE_THRESHOLD` (`0.85`) est listée dans `low_confidence_fields` et déclenche `needs_manual_review = True` pour confirmation étudiante/humaine dans `Bonus-2`.

---

## 2. Contrat JSON `Bonus-1` -> `Bonus-2` (Mapping PostgreSQL)

Le schéma Pydantic `BacTranscriptExtractionResult` (`backend/app/schemas/transcript.py`) s'aligne directement sur les tables SQL créées en `SA-17` (`backend/app/models/users.py`) :

| Champ JSON (`Bonus-1`) | Table & Colonne cible (`Bonus-2`) | Contrainte SQL vérifiée |
| :--- | :--- | :--- |
| `document_type` (`"RELEVE_BAC"`) | `core.document.type` | `IN ('RELEVE_BAC', ...)` |
| Payload JSON complet | `core.document.ocr_json` | `JSONB` |
| `min_confidence` | `core.document.min_confidence` | `0.000 <= x <= 1.000` |
| `verification_status` (`PENDING` / `MISMATCH_RH`) | `core.document.verification_status` | `IN ('PENDING', 'CONFIRMED_BY_STUDENT', 'MISMATCH_RH', 'VERIFIED')` |
| `bac_section` | `core.candidate_profile.current_specialty` | Section officielle Bac Tunisien |
| `general_average` / `calculated_average` | `core.candidate_profile.declared_overall_avg` | `0.00 <= x <= 20.00` |
| `subjects[i].name` | `core.declared_grades.subject_name` | `VARCHAR(128)` |
| `subjects[i].grade` | `core.declared_grades.grade_value` | `0 <= grade_value <= grade_scale` |
| `subjects[i].coefficient` | `core.declared_grades.coefficient` | `coefficient > 0` |
| `subjects[i].confidence` | `core.declared_grades.confidence_score` | `0.000 <= x <= 1.000` |
| `diploma_stage` (`"BAC"`) | `core.declared_grades.diploma_stage` | `IN ('BAC', 'L1', ...)` |

---

## 3. Jeux d'Essais Tunisiens (Sfax) Conformes INPDP (Loi 2004-63)

Afin de respecter strictement la loi tunisienne sur la protection des données personnelles (INPDP 2004-63), aucun relevé contenant la CIN ou le nom réel d'un étudiant n'est commité dans le dépôt. Trois jeux d'essais synthétiques anonymisés fidèles au format officiel du Ministère de l'Éducation (Gouvernorat de Sfax) sont générés par `backend/scripts/generate_sample_bac_fixtures.py` dans `backend/tests/data/sample_bac/` :

1. `releve_bac_math_sfax_2026.pdf` — Lycée Pilote de Sfax, Section **Mathématiques** (9 matières, coefficients officiels `4, 4, 1, 1, 1, 1, 1, 1, 1`, Moyenne `16.80`, Mention *Très Bien*).
2. `releve_bac_info_sfax_2026.pdf` — Lycée Hédi Chaker Sfax, Section **Sciences de l'Informatique** (9 matières, coefficients officiels `3, 3, 3, 2, 1, 1, 1, 1, 1`, Moyenne `15.45`, Mention *Bien*).
3. `releve_bac_math_sfax_2026.png` — Scan PNG haute résolution (920×1120 px) du relevé anonymisé de Sfax pour les tests Vision multimodaux.
