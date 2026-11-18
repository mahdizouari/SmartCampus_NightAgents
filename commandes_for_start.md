# Shared Team Commands — SmartCampus AI (Night Agents)

Ordered command reference for the **Night Agents** team. Run all commands from the **repository root** (`SmartCampus_NightAgents/`).

---

## 1. One-Time Setup (Run Once After `git clone`)

```bash
# Step 1 — Enable the shared Git pre-push hook (checks .env leaks, lint, and tests before push)
git config core.hooksPath .githooks

# Step 2 — Create your local root .env file from the template (used by Docker Compose)
cp .env.example .env

# Step 3 (Optional) — Create local Python 3.12 virtualenv for IDE autocomplete/Pyright
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

> **Note on `.env` files:** When using Docker Compose, you only need the **root `.env`**. Only copy `backend/.env.example` → `backend/.env` and `frontend/.env.example` → `frontend/.env.local` if you run FastAPI (`uvicorn`) or Next.js (`npm run dev`) directly on your PC outside Docker.

---

## 2. Daily Git & Branch Workflow

**Branch naming convention:** `<member>/SA-XX-<short-description>` (or `<member>/Bonus-X-<short-description>`), lowercase with hyphens.
- Examples: `mehdi/SA-28-auth-jwt`, `moataz/SA-17-sql-tables`, `yassine/Bonus-3-whisper`, `melek/SA-7-fastapi-init`, `youssef/SA-8-nextjs-init`

```bash
# Step 1 — Fetch remote updates, clean deleted branch refs, and pull latest dev
git fetch origin --prune
git checkout dev && git pull origin dev

# Step 2 — Create your task branch from dev
git checkout -b <member>/SA-XX-<short-description>

# Step 3 — Stage, commit, and push your branch (pre-push hook runs automatically)
git add .
git commit -m "feat(SA-XX): short summary of changes"
git push -u origin <member>/SA-XX-<short-description>
```
Then open a Pull Request into **`dev`** on GitHub and include `Closes #<issue_number>` in the description.

---

## 3. Docker — Build, Start & Manage Containers

### 3.1 Full Stack Startup (In Order)

```bash
# Step 1 — Ensure root .env exists (without overwriting your existing secrets)
[ -f .env ] || cp .env.example .env

# Step 2 — Build images and start all 4 containers (postgres, redis, backend, frontend)
#          Note: backend/entrypoint.sh automatically runs `alembic upgrade head` on startup
docker compose up -d --build

# Step 3 (Optional) — Run Alembic migrations manually or seed SA-18 demo data
docker compose exec backend alembic upgrade head
docker compose exec backend python scripts/seed_db.py

# Step 4 — Verify container status and DB + pgvector health
docker compose ps
curl http://localhost:8000/health/db
```

**Local Service URLs:**
| Service | URL / Port |
| :--- | :--- |
| **Frontend (Next.js)** | `http://localhost:3000` |
| **Backend Swagger UI (FastAPI)** | `http://localhost:8000/docs` |
| **Backend + DB + pgvector Health** | `http://localhost:8000/health/db` |
| **PostgreSQL 16 (host port)** | `localhost:5433` (`smartcampus_db`) |
| **Redis 7 (host port)** | `localhost:6379` |

### 3.2 Daily Container Controls (All Services vs Single Service)

```bash
# Start / stop all containers (keeps PostgreSQL & Redis data volumes)
docker compose up -d
docker compose down

# Follow live logs (all services or one specific service; Ctrl+C to exit)
docker compose logs -f
docker compose logs -f backend

# Build or start ONE specific service (postgres | redis | backend | frontend)
docker compose build backend
docker compose up -d backend

# Rebuild & restart ONLY one service without touching its dependencies
docker compose up -d --no-deps --build frontend

# Restart or stop one specific service
docker compose restart backend
docker compose stop frontend
```

---

## 4. Testing & Linting (`ruff` & `pytest` in Docker)

Run these while the `backend` container is running:

```bash
# Run both Ruff linter and full Pytest suite together
docker compose exec backend sh -c "ruff check . && pytest -v"

# Run Ruff linter alone (add --fix to auto-fix formatting/import issues)
docker compose exec backend ruff check .
docker compose exec backend ruff check . --fix

# Run all unit & integration tests
docker compose exec backend pytest -v

# Run a single test file
docker compose exec backend pytest -v tests/test_auth.py
docker compose exec backend pytest -v tests/test_catalog_management.py
```

---

## 5. Clean Rebuild & Troubleshooting

### 5.1 Clean Rebuild of SmartCampus Containers Only (Keeps DB Data)

```bash
docker compose down --rmi local --remove-orphans
docker compose build --no-cache
docker compose up -d
```

### 5.2 Reset PostgreSQL Volume (Re-runs `docker/postgres/01-init.sql` + Migrations)

> **Warning:** `-v` deletes local PostgreSQL and Redis data volumes.

```bash
docker compose down -v --remove-orphans
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python scripts/seed_db.py
```

### 5.3 Full Docker Cache Prune (If Docker Build Cache Gets Corrupted)

```bash
docker compose down --remove-orphans
docker container prune -f
docker builder prune -f
docker image prune -f
docker compose up -d --build
```

---

## 6. GitHub Backlog & Project Board Setup (Team Lead Only)

```bash
# Install GitHub CLI (Fedora / Ubuntu)
sudo dnf install -y gh
# sudo apt update && sudo apt install -y gh

# Authenticate and sync labels, milestones, and Sprint 1 issues
gh auth login
bash scripts/setup_github_backlog.sh

# Create & link the GitHub Project board
gh auth refresh -s project
gh project create --owner mahdizouari --title "SmartCampus AI — Night Agents"
gh project link $(gh project list --owner mahdizouari --format json --jq '.projects[0].number') \
  --owner mahdizouari --repo mahdizouari/SmartCampus_NightAgents
```
