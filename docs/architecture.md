# U-FirstSupport — Shared Domain Architecture

This file is referenced by both the backend and frontend repos via `@docs/architecture.md`.
Keep it in sync across both repos (or host in a shared docs repo and symlink).

## Domain overview
U-FirstSupport enables diaspora members (Sponsors) to fund prepaid cards used by family (Beneficiaries) at registered merchants (Vendors) back home. The platform handles: KYC, card issuance, wallet management, open banking funding, card transaction authorization, vendor settlement, and compliance.

## Actors
- **Sponsor**: Lives abroad. Funds cards. Sees all transactions. Sets spending controls.
- **Beneficiary**: Lives in home country. Uses card at POS. Views balance.
- **Vendor**: Registered grocery/supermarket/restaurant. Accepts cards. Receives settlements.
- **Ops/Compliance**: Internal team managing vendors, reviewing flagged transactions, filing SARs.

## Modules (backend)
Each module is a bounded context with its own DB schema, service interface, and migration directory.

| Module | Owns | Key API endpoints |
|--------|------|-------------------|
| identity | Users, KYC, sponsor-beneficiary links | /auth/*, /users/*, /users/me/beneficiaries, /kyc/*, /onboarding/complete-profile |
| wallet | Wallets, ledger, funding transfers, bank connections | /wallets/*, /funding/* |
| card | Card lifecycle, processor tokens | /cards/* |
| transaction | Authorizations, clearings, disputes | /transactions/* |
| vendor | Vendor profiles, locations, settlements | /vendors/*, /settlements/* |
| compliance | Screening, SARs, alerts, rules engine | /compliance/* (admin only) |
| notification | SMS, push, email delivery | Internal only (no public API) |
| reporting | Analytics, reconciliation | /reports/* (admin only) |

## Authentication (Supabase)
- Supabase handles: login (email/password, phone OTP), MFA (TOTP), session management, social OAuth
- Frontend uses `@supabase/supabase-js` to authenticate and obtain JWTs
- Backend verifies JWTs locally using Supabase's JWKS endpoint (cached, no per-request call)
- `auth.users.id` (UUID from Supabase) is the canonical user identifier across all backend schemas
- Roles stored in Supabase `app_metadata.role`: sponsor, beneficiary, vendor_admin, vendor_cashier, ops_agent, compliance_officer, admin
- Backend uses lazy provisioning: auth middleware checks if `identity.users` exists for the JWT's `sub` claim on every request. If missing, creates a skeleton record from JWT claims (UUID, email, role). Frontend then calls `POST /onboarding/complete-profile` to fill in country, phone, and relationship data. No webhook dependency.
- **Beneficiary provisioning**: Sponsors create beneficiary accounts via `POST /users/me/beneficiaries` — the backend generates a UUID and creates the `identity.users` record directly. Beneficiaries do not self-register; they are added by their sponsor.

## Key financial rules (both repos must respect)
- All monetary amounts are **integers in minor currency units** (cents, kobo, etc.)
- API sends/receives amounts as `{ "amount": 5000, "currency": "GBP" }` meaning £50.00
- Frontend MUST use the shared `formatCurrency(amount, currency)` for display
- Backend MUST use `int` or `Decimal` — never `float` — for all calculations
- The ledger is append-only. No UPDATEs or DELETEs on `ledger_entries`.
- Every financial API call requires an `Idempotency-Key` header

## Open banking funding flow (both repos involved)
1. Sponsor taps "Fund card" → frontend calls `POST /funding/initiate`
2. Backend: validates permissions, runs AML screen, locks FX rate, calls TrueLayer → returns `auth_link`
3. Frontend: opens `auth_link` in in-app browser for bank SCA
4. Sponsor authenticates with their bank, bank executes payment
5. TrueLayer sends webhook to backend → backend credits wallet
6. Frontend: polls `/funding/{id}/status` until COMPLETED → shows success

**Critical**: Frontend MUST NOT show "Funded!" until backend confirms `status: "COMPLETED"`. The redirect callback alone does not confirm payment.

## Funding transfer states
```
INITIATED → AWAITING_AUTHORIZATION → AUTHORIZING → AWAITING_SETTLEMENT → COMPLETED
                  ↓                       ↓                ↓
               EXPIRED                  FAILED           FAILED
                  ↓
              CANCELLED
```

## Card transaction states
```
AUTHORIZATION → CLEARING → SETTLEMENT
      ↓             ↓
   DECLINED      REVERSAL
```

## API conventions
- All endpoints versioned: `/api/v1/...`
- Auth: `Authorization: Bearer <supabase_jwt>`
- Idempotency: `Idempotency-Key: <uuid>` header on all POST/PUT/PATCH
- Pagination: `?page=1&per_page=20` → response includes `{ data: [], meta: { total, page, per_page } }`
- Errors: `{ "error": { "code": "INSUFFICIENT_BALANCE", "message": "...", "details": {} } }`
- Dates: ISO 8601 UTC (`2026-03-18T14:30:00Z`)
- Money: `{ "amount": 5000, "currency": "GBP" }` (integer minor units)

## Environment separation
- `development`: Local, Supabase local emulator, test PostgreSQL
- `staging`: Render preview environment, Supabase staging project, TrueLayer sandbox
- `production`: Render production, Supabase production project, TrueLayer live
