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
│   ├── models/                  # Modèles ORM SQLAlchemy 2.0 multi-schémas (SA-17)
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
├── alembic/                     # Migrations PostgreSQL asynchrone + RLS (SA-17)
│   ├── env.py
│   └── versions/
├── scripts/
│   └── verify_schema.py         # Vérification live schémas, 40 tables, FK, HNSW et RLS
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Fixtures Pytest (TestClient)
│   ├── test_health.py           # Tests unitaires pour /health
│   ├── test_database.py         # Tests unitaires pool, DB config & /health/db (SA-16)
│   └── test_migrations.py       # Tests offline métadonnées, ENUMs, FKs et SQL Alembic (SA-17)
├── alembic.ini                  # Configuration Alembic
├── .env.example                 # Modèle de variables d'environnement
├── requirements.txt             # Dépendances Python (FastAPI, SQLAlchemy, Alembic, asyncpg...)
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

---

## Base de données & Migrations Alembic (`SA-17`)

Depuis la racine du projet, démarrer PostgreSQL 16 (`pgvector`) sur une instance fraîche si besoin :

```bash
docker compose down -v
docker compose up -d postgres
```

Puis depuis `backend/` (avec `DATABASE_URL` configurée dans `backend/.env` sur `localhost:5433`) :

```bash
# Appliquer les migrations (40 tables + index HNSW/GIN + politiques RLS)
alembic upgrade head

# Générer le script SQL DDL sans connexion DB
alembic upgrade head --sql

# Vérifier sur PostgreSQL live : 5 schémas, 40 tables, FKs, contraintes et isolation RLS/HITL
python scripts/verify_schema.py
```

