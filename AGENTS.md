# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

# U-FirstSupport — API Backend

## What this is
Modular monolith API for a diaspora prepaid card platform. Sponsors (abroad) fund cards that beneficiaries (back home) use at registered vendors. We sit in the middle: KYC, card issuance, wallet management, transactions, compliance.

## Architecture
See @docs/architecture.md for full domain context shared with the frontend repo.

- **Monolith with 8 internal modules**: identity, wallet, card, transaction, vendor, compliance, notification, reporting
- **Layout**: `app/modules/<module>/` — each contains service.py (public interface), models.py, routes.py, repository.py, events.py
- **Modules communicate** through typed in-process event bus and public service interfaces — NEVER import internals from another module
- **Database**: Single PostgreSQL, schema-per-module (`identity.*`, `wallet.*`, `card.*`, etc.). Each schema has a dedicated DB role.
- **No cross-schema SQL**. If wallet needs user data, it calls `IdentityService.get_user()`, not `SELECT FROM identity.users`.
- **Migrations**: per-module directories `migrations/<module>/`. Run `alembic upgrade head` to apply all.

## Stack
- Python 3.12 + FastAPI
- SQLAlchemy 2.x (async) with Alembic migrations
- Redis (via `redis-py`) for cache, sessions, job queues
- Celery for background jobs (worker + beat scheduler)
- Supabase for authentication (JWT verification only — we verify locally via JWKS, never call Supabase per-request)
- Pydantic v2 for all request/response schemas
- pytest + pytest-asyncio for tests

## Commands
```bash
# Dev server
uvicorn app.main:app --reload --port 8000

# Run a single test
pytest tests/modules/wallet/test_fund_wallet.py::test_name -v

# Run tests for one module
pytest tests/modules/wallet/ -v

# Run all tests
pytest --tb=short

# Type check
mypy app/ --strict

# Lint + format
ruff check app/ && ruff format app/

# Run migrations
alembic upgrade head

# Create migration (specify module)
alembic revision --autogenerate -m "wallet: add ob_payments table"

# Start celery worker
celery -A app.jobs.celery_app worker -Q critical,default,bulk -l info

# Start celery beat (scheduler)
celery -A app.jobs.celery_app beat -l info
```

## Code style
- Strict typing everywhere. Every function has full type annotations. Use `from __future__ import annotations`.
- Pydantic models for all API input/output. No raw dicts crossing module boundaries.
- Async by default for route handlers and DB operations.
- Module service methods are the public API. They return typed dataclasses/Pydantic models, not ORM objects.
- All financial amounts are `int` in minor units (cents/kobo). Never use `float` for money.
- Ledger entries are append-only. The `ledger_entries` table has no UPDATE or DELETE. Corrections use reversal entries.
- Every external API call goes through a dedicated client class in the module (e.g., `wallet/openbanking/client.py`). Never call `httpx` directly from a service method.

## Testing
- One test file per service method: `tests/modules/wallet/test_fund_wallet.py`
- Use `pytest.fixture` for DB sessions, test users, test wallets
- Mock external APIs (Supabase, TrueLayer, card processor) at the client class level, not at httpx level
- Financial tests MUST assert ledger balance consistency: sum of debits == sum of credits for every test case
- Integration tests against real PostgreSQL (use testcontainers or Render preview DBs)

## IMPORTANT rules
- NEVER store raw card PANs. Only processor-issued tokens. If you see a raw card number in code, that is a critical security bug.
- NEVER use `float` for financial calculations. Always `int` (minor units) or `Decimal` with explicit rounding.
- NEVER query across module schemas. Call the owning module's service interface.
- Every webhook endpoint MUST verify the inbound signature before processing.
- Every financial operation MUST include an idempotency_key check.
- Migrations are per-module (`migrations/wallet/`, `migrations/identity/`, etc.) — always name them with the module prefix: `"wallet: add ob_payments table"`.
