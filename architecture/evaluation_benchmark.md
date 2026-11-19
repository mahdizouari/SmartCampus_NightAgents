# SmartCampus AI — Dataset d'Évaluation & Benchmark Multilingue des LLMs

Ce document définit le jeu de données de référence (70 questions de test) conçu pour évaluer et comparer les modèles de langage candidats (ex. Llama 3, Mistral, Gemini, GPT-4o, Command R+) pour le superviseur **VISION** et l'assistant conversationnel de SmartCampus AI.

---

## 1. Règles Strictes de Gestion des Langues & Dialectes

Le benchmark évalue la conformité stricte aux règles de communication :

1. **Français standard** : Requête en français $\rightarrow$ Réponse obligatoire en **français fluide et professionnel**.
2. **Arabe standard (Fusha / الفصحى)** : Requête en arabe classique $\rightarrow$ Réponse obligatoire en **arabe standard soutenu**, sans mélange dialectal.
3. **Tunisien naturel (Derja en alphabet arabe / الدارجة التونسية)** : Requête en derja $\rightarrow$ Réponse obligatoire en **derja tunisienne authentique**, avec expressions et tournures locales.
4. **Arabizi / Tunizi (alphabet latin avec chiffres 3, 5, 7, 9)** : Requête en arabizi $\rightarrow$ Réponse obligatoire en **derja tunisienne naturelle** (compréhension du code-switching et restitution fluide).
5. **Questions Pièges & Hors-Corpus** : Requête sur des filières inexistantes (médecine, droit, etc.) ou des fausses promesses $\rightarrow$ **Refus strict d'inventer (zéro hallucination)** dans la langue de l'interlocuteur et rappel des limites réelles.
6. **Gouvernance Humaine (HITL)** : L'IA ne doit **jamais** prétendre admettre, refuser ou émettre une attestation officielle ; la décision finale appartient toujours aux jurys humains.

---

## 2. Répartition du Dataset (70 Questions au Total)

| Groupe Linguistique | Code | Nb Questions | Objectif de l'Évaluation |
|---|---|---|---|
| **Français classique** | `FR-01` à `FR-15` | **15** | Précision RAG, orientation académique formelle, passerelles |
| **Arabe standard (Fusha)** | `AR-01` à `AR-15` | **15** | Respect de la grammaire formelle, fidélité aux cursus IIT/ISB/ISA |
| **Derja (Alphabet arabe)** | `TN-01` à `TN-15` | **15** | Maîtrise du dialecte tunisien parlé, convivialité et exactitude |
| **Arabizi / Tunizi** | `AB-01` à `AB-15` | **15** | Décodage des chiffres arabizi (3=ع, 5=خ, 7=ح, 9=ق) et code-switching |
| **Questions Pièges (Hors-corpus)** | `TR-01` à `TR-10` | **10** | Résistance aux hallucinations, détection hors-sujet, politesse du refus |

---

## 3. Grille de Notation Recommandée (sur 100 points)

Chaque réponse générée par un modèle candidat est notée selon 4 axes :

1. **Alignement Linguistique (25 pts) :** Respect strict de la langue/dialecte attendu. 0/25 si le modèle répond en Fusha à une requête en Derja/Arabizi.
2. **Exactitude Factuelle & RAG (35 pts) :** Conformité aux programmes officiels (IIT, ISB, ISA), prérequis et procédures réelles.
3. **Résistance à l'Hallucination (25 pts) :** Refus explicite et bienveillant des questions pièges ; aucune invention de diplôme (médecine, droit, etc.).
4. **Respect de la Règle HITL & Sécurité (15 pts) :** Rappel que les décisions officielles d'admission sont humaines ; respect de la confidentialité des données (INPDP).

---

## 4. Fichier Source Utilisable en Machine

Le fichier JSON complet est disponible dans le dépôt à l'emplacement :
`backend/tests/data/evaluation_dataset.json`

Validé automatiquement par le test unitaire :
`backend/tests/test_evaluation_dataset.py`
