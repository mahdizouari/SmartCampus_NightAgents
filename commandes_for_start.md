# Shared Team Commands — SmartCampus AI (Night Agents)

A shared runbook of commands every team member needs (one-time setup, Git/CI, Docker, and database/backend shortcuts).

---

## 1. One-Time Setup (Run Once After `git clone`)

Enable the shared local Git pre-push hook (automatically checks `.env` leaks, script syntax, lint, and tests before `git push`):

```bash
git config core.hooksPath .githooks
```

---

## 2. GitHub Backlog Setup (Team Lead Only)

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

---

## 3. Daily Git Workflow

```bash
# Always pull latest dev/main before starting a task
git checkout main && git pull origin main

# Create a branch for your issue (example: SA-28)
git checkout -b feat/SA-28-auth-jwt

# Push your branch (pre-push hook runs automatically)
git push -u origin feat/SA-28-auth-jwt
```
