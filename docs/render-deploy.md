# Deploying to Render

## Resources to create

You need four Render resources in this order:
1. PostgreSQL
2. Key-Value (Redis)
3. Web Service (API)
4. Background Worker (Celery)

---

## 1. PostgreSQL

**Dashboard → New → PostgreSQL**

| Field | Value |
|---|---|
| Name | `ufirst-db` |
| Region | `Frankfurt (EU Central)` — match your Supabase region |
| Plan | Free (staging) / Starter (production) |

After creation copy the **Internal Database URL** — you need it in step 3.

---

## 2. Key-Value (Redis)

**Dashboard → New → Key-Value**

| Field | Value |
|---|---|
| Name | `ufirst-redis` |
| Region | Same as above |
| Plan | Free (staging) / Starter (production) |

After creation copy the **Internal Redis URL** — you need it in step 3.

---

## 3. Environment Group

Create a shared group so both the web service and worker share one set of vars.

**Dashboard → Environment Groups → New Environment Group**

Name it `ufirst-env` then add every variable below.

### Required

| Key | Value |
|---|---|
| `DATABASE_URL` | Internal Database URL from step 1 |
| `REDIS_URL` | Internal Redis URL from step 2 |
| `ENCRYPTION_KEY` | Run `openssl rand -hex 32` once — save it, never regenerate |
| `SUPABASE_JWKS_URL` | `https://yzzwugveurqkqhmegigi.supabase.co/auth/v1/.well-known/jwks.json` |
| `SUPABASE_URL` | `https://yzzwugveurqkqhmegigi.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase dashboard → Settings → API → service_role key |
| `APP_BASE_URL` | `https://<your-render-service>.onrender.com` (fill after step 4) |
| `FRONTEND_URL` | Your frontend URL |
| `DEBUG` | `false` |
| `DEV_MODE` | `false` |

### Open Banking (Yapily)

| Key | Value |
|---|---|
| `OPENBANKING_PROVIDER` | `YAPILY` |
| `YAPILY_APPLICATION_ID` | From Yapily dashboard |
| `YAPILY_APPLICATION_SECRET` | From Yapily dashboard |
| `YAPILY_WEBHOOK_SECRET` | From Yapily dashboard |
| `YAPILY_PAYEE_NAME` | Your merchant name |
| `YAPILY_PAYEE_SORT_CODE` | UK sort code |
| `YAPILY_PAYEE_ACCOUNT_NUMBER` | UK account number |
| `YAPILY_PAYEE_IBAN` | IBAN for SEPA payments |

### Card Processor (UP Nigeria)

| Key | Value |
|---|---|
| `UP_NIGERIA_API_KEY` | From UP Nigeria — leave blank to use dev stub |
| `UP_NIGERIA_CARD_PROGRAM_ID` | Assigned after UP Nigeria onboarding |
| `UP_NIGERIA_WEBHOOK_SECRET` | From UP Nigeria |

### Stripe (optional fallback)

| Key | Value |
|---|---|
| `STRIPE_SECRET_KEY` | From Stripe dashboard → Developers → API keys |
| `STRIPE_WEBHOOK_SECRET` | From Stripe dashboard → Webhooks |

---

## 4. Web Service (API)

**Dashboard → New → Web Service** → connect `effaamponsah/ufirst-backend`

| Field | Value |
|---|---|
| Name | `ufirst-api` |
| Region | Same as above |
| Runtime | Python 3 |
| Branch | `main` |
| Build Command | `pip install -e .` |
| Start Command | `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |
| Plan | Free (staging) / Starter (production) |

Under **Environment** → link the `ufirst-env` group.

> `alembic upgrade head` runs before uvicorn starts. It is idempotent — if there are no pending migrations it exits in under a second.

After the service is live, copy its URL and update `APP_BASE_URL` in the environment group.

---

## 5. Background Worker (Celery)

**Dashboard → New → Background Worker** → same repo

| Field | Value |
|---|---|
| Name | `ufirst-worker` |
| Runtime | Python 3 |
| Branch | `main` |
| Build Command | `pip install -e .` |
| Start Command | `celery -A app.jobs.celery_app worker -Q critical,default,bulk -l info` |
| Plan | Free (staging) / Starter (production) |

Under **Environment** → link the same `ufirst-env` group.

---

## Deploy order

1. Create PostgreSQL → Key-Value
2. Create Environment Group with all vars
3. Deploy Web Service — migrations run automatically on first boot
4. Deploy Background Worker

---

## After deploying

- Update `APP_BASE_URL` in the environment group to the live web service URL
- Update webhook URLs in Yapily / Stripe / UP Nigeria dashboards to point to `https://<your-service>.onrender.com/api/v1/webhooks/...`
- Rotate any secrets that were previously committed to git
