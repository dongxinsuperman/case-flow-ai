# Contributing

Thanks for your interest in Case Flow.

Case Flow is an open-source AI-assisted test case workbench. The
public repository should stay free of private endpoints, private tokens, private
CI files, company-only report formats, and company-specific deployment details.

## Development Setup

Backend:

```bash
docker compose -f docker/docker-compose.yml up -d postgres
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e "backend[dev]"
cp backend/.env.example backend/.env
cd backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8800
```

Frontend:

```bash
cd web
npm install
npm run dev
```

## Before Opening a Pull Request

Run the checks that match the files you changed:

```bash
backend/.venv/bin/python -m pytest backend/tests
cd web && npm run build
```

Also check:

- Database changes include an Alembic migration under `backend/alembic/versions/`.
- API changes update `docs/API契约.md`.
- Schema changes update `docs/数据模型.md`.
- Product behavior changes update `docs/功能说明.md` and the relevant public integration guide.
- Public documentation must not link local design drafts, real-environment records, or other Git-ignored files.
- No real credentials, internal service addresses, report screenshots, or private config files are committed.

## Scope Boundaries

Keep enterprise or company-specific integrations outside this repository unless
they are represented as generic configuration templates or extension points.

Examples that should not be committed:

- Real Feishu project config: `backend/config/feishu_project.json`
- Real Feishu issue config: `backend/config/feishu_issue.json`
- Local env files: `backend/.env`
- Runtime media and report images: `backend/var/`

## Commit Style

Use concise imperative commit messages, for example:

```text
Add bug submission workflow
Use AI Phone favicon
```

Keep unrelated changes in separate commits.
