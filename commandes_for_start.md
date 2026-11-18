# Shared Team Commands — SmartCampus AI (Night Agents)

A shared, ordered runbook of commands every team member needs (one-time setup, Docker & DB startup, daily Git workflow, and GitHub backlog tools).

---

## 1. One-Time Setup (Run Once After `git clone`)

Run all commands below from the **repository root** (`SmartCampus_NightAgents/`):

```bash
# 1. Enable the shared local Git pre-push hook (checks .env leaks, lint, tests before push)
git config core.hooksPath .githooks

# 2. Create the root .env file (used by Docker Compose for both backend and frontend)
cp .env.example .env

# 3. (Optional, recommended for IDE linting/autocomplete) Create local Python 3.12 venv
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

> **Note:** With Docker Compose, you only need the **root `.env`**. Only copy `backend/.env.example` → `backend/.env` and `frontend/.env.example` → `frontend/.env.local` if you run FastAPI or Next.js directly on your machine without Docker.

---

## 2. Build, Start Containers & Run Migrations (In Order)

```bash
# Step 1 — Pull latest dev
git checkout dev && git pull origin dev

# Step 2 — Ensure root .env exists (without overwriting your secrets), then build & start
#          all 4 containers (postgres pgvector, redis, backend, frontend)
[ -f .env ] || cp .env.example .env
docker compose up -d --build

# Step 3 — Run Alembic database migrations (creates all schemas/tables in PostgreSQL)
docker compose exec backend alembic upgrade head

# Step 4 — Verify all containers are healthy and check API/DB connectivity
docker compose ps
curl http://localhost:8000/health/db
```

**Service URLs:**
- Frontend (Next.js): `http://localhost:3000`
- Backend Swagger UI (FastAPI): `http://localhost:8000/docs`
- Backend + pgvector Health Check: `http://localhost:8000/health/db`
- PostgreSQL (host port): `localhost:5433` (`smartcampus_db`)

### Useful Daily Docker & Test Commands

```bash
# Follow live container logs (Ctrl+C to exit)
docker compose logs -f

# Run Ruff lint + Pytest inside the backend container
docker compose exec backend sh -c "ruff check . && pytest -v"

# Rebuild backend/frontend after editing requirements.txt or package.json
docker compose up -d --build backend frontend

# Stop all containers (keeps PostgreSQL/Redis volumes)
docker compose down

# Reset PostgreSQL volume ONLY if docker/postgres/01-init.sql changed (re-runs init SQL)
docker compose down -v && docker compose up -d --build
docker compose exec backend alembic upgrade head
```

---

## 3. Daily Git Workflow

Branch naming convention: `<member>/SA-XX-<short-description>` (or `<member>/Bonus-X-<short-description>`), lowercase with hyphens.

Examples:
- `mehdi/SA-20-catalog-api`
- `moataz/SA-17-sql-tables`
- `yassine/Bonus-3-whisper`
- `melek/SA-7-fastapi-init`
- `youssef/SA-8-nextjs-init`

```bash
# 1. Fetch updates, prune deleted remote branches, and pull latest dev
git fetch origin --prune
git checkout dev && git pull origin dev

# 2. Create your task branch from dev
git checkout -b moataz/SA-17-sql-tables

# 3. Work, commit, and push (pre-push hook runs automatically), then open a PR into dev
git push -u origin moataz/SA-17-sql-tables
```

---

## 4. GitHub Backlog Setup (Team Lead Only)

Install GitHub CLI (`gh`), authenticate, and sync labels, milestones, and Sprint 1 issues:

```bash
# Fedora:
sudo dnf install -y gh
# Ubuntu / Debian:
# sudo apt update && sudo apt install -y gh

gh auth login
bash scripts/setup_github_backlog.sh
```

Create & link the GitHub Project board:

```bash
gh auth refresh -s project
gh project create --owner mahdizouari --title "SmartCampus AI — Night Agents"
gh project link $(gh project list --owner mahdizouari --format json --jq '.projects[0].number') \
  --owner mahdizouari --repo mahdizouari/SmartCampus_NightAgents
```
