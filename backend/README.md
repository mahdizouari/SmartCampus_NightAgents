# SmartCampus AI — Backend Service

FastAPI-powered backend for **SmartCampus AI** (Groupe 3 « Night Agents »).

---

## 📁 Architecture & Arborescence

```text
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # Point d'entrée FastAPI, CORS, montage des routeurs, lifespan
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # Pydantic Settings, DB config & chargement .env
│   │   └── database.py          # SQLAlchemy async engine, connection pool & get_db (SA-16)
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py        # Agrégateur des routes v1
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           └── health.py    # GET /health & /api/v1/health/db
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── health.py            # Schémas de validation Pydantic (Health & DatabaseHealth)
│   └── services/
│       └── __init__.py          # Services métier et intégrations
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Fixtures Pytest (TestClient)
│   ├── test_health.py           # Tests unitaires pour /health
│   └── test_database.py         # Tests unitaires pool, DB config & /health/db (SA-16)
├── .env.example                 # Modèle de variables d'environnement
├── requirements.txt             # Dépendances Python (FastAPI, SQLAlchemy, asyncpg...)
├── ruff.toml                    # Configuration du linter Ruff
└── README.md
```

---

## 🚀 Démarrage Rapide

### 1. Prérequis & Environnement Virtuel

```bash
cd backend
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Linux / macOS:
source .venv/bin/activate
```

### 2. Installation des Dépendances

```bash
pip install -r requirements.txt
```

### 3. Lancement du Serveur de Développement

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **API Root :** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Health Check :** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- **Database & pgvector Check :** [http://127.0.0.1:8000/health/db](http://127.0.0.1:8000/health/db)
- **Documentation Swagger interactive :** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Documentation ReDoc :** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## 🧪 Tests & Linting

Conformément aux vérifications CI et au pre-push hook du dépôt :

```bash
# Vérification du code avec Ruff
ruff check .

# Exécution des tests unitaires avec Pytest
pytest -v
```

