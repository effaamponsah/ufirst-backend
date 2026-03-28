# Review Findings

Use this file as the running log for review issues. Add new findings under the
phase they belong to so they are easy to pick up during implementation.

## Phase 0 — Project Foundation

No findings recorded yet.

## Phase 1 — Identity Module

### 2026-03-19

#### Resolved

##### [DONE] [P1] Sponsor-only beneficiary endpoints did not enforce sponsor role
Original file: `app/modules/identity/routes.py:121`

`GET /users/me/beneficiaries` and `DELETE /users/me/beneficiaries/{beneficiary_id}` relied only on `get_current_user`. Fixed by adding `require_roles("sponsor")` as a dependency on both endpoints.

##### [DONE] [P0] KYC webhook mutated user status without verifying a signature
Original file: `app/modules/identity/routes.py:173`

`POST /kyc/webhook` accepted arbitrary JSON with no signature check. Fixed by implementing HMAC-SHA256 verification via `_verify_kyc_signature()` using `KYC_WEBHOOK_SECRET`. Fails closed (500) when the secret is unset in non-dev mode.

##### [DONE] [P1] Supabase webhook verification failed open when the secret was unset
Original file: `app/modules/identity/routes.py:37`

The Supabase webhook verifier returned early when `SUPABASE_WEBHOOK_SECRET` was blank. Resolved by removing the webhook endpoint entirely — user provisioning is now lazy (auth middleware creates `identity.users` on first authenticated request via `IdentityService.get_or_create_user()`). The `POST /onboarding/complete-profile` endpoint fills in profile data.

##### [DONE] [P1] Lazy provisioning middleware failed open on provisioning errors
Original file: `app/modules/identity/middleware.py:46`

The middleware swallowed `PermissionDenied` and generic exceptions and let the request continue without an identity row. Fixed by catching `UFirstError` subclasses and returning their HTTP status/body directly from the middleware, and catching all other exceptions and returning 500. Only `AuthenticationError` (bad/expired JWT) is passed through so the route handler can return the correct 401.

##### [DONE] [P1] Test setup never applied the new identity migration
Original file: `tests/conftest.py:42`

`Base.metadata.create_all()` is a no-op for tables that already exist, so new columns (`country`, `beneficiary_relationship`) were silently absent on non-pristine databases. Fixed by calling `drop_all` before `create_all` in the session-scoped `db_engine` fixture, guaranteeing the schema always matches the current ORM models.

#### Open

##### [DONE] [P0] Lazy provisioning still breaks on blank-email users
Original file: `app/modules/identity/service.py:65`

The middleware now fails closed correctly, but `IdentityService.get_or_create_user()` still inserts `email=current_user.email` directly, while `verify_token()` produces `email=""` for dev tokens and for JWTs without an `email` claim. Because `identity.users.email` is still unique and non-null, the second blank-email user now fails with a 500 from the middleware due to `uq_users_email`. This still breaks the new lazy-provisioning path for phone-only users and multi-user dev/test flows.

## Phase 2 — Wallet Module

### 2026-03-19

#### Resolved

##### [DONE] [P0] Funding idempotency key was global across all sponsors
Original file: `app/modules/wallet/service.py:128`

Originally, `initiate_funding()` looked up transfers by raw `idempotency_key` alone and the schema enforced a global unique constraint on that column, which allowed cross-tenant collisions and data leakage. This has been addressed by scoping lookup and uniqueness to `(sponsor_id, idempotency_key)`.

##### [DONE] [P1] Any authenticated user could read any funding transfer by UUID
Original file: `app/modules/wallet/routes.py:96`

Originally, `GET /funding/{transfer_id}` only required authentication and returned any transfer by UUID without ownership or role checks. This has been addressed by restricting access to the owning sponsor or privileged roles.

##### [DONE] [P1] Sponsors could fund arbitrary wallets without an active sponsor-beneficiary link
Original file: `app/modules/wallet/routes.py:74`

Originally, supplying `beneficiary_wallet_id` bypassed link verification and allowed funding of any wallet UUID. This has been addressed by loading the beneficiary wallet owner and calling `IdentityService.verify_sponsor_beneficiary_link()` before initiating funding.

##### [DONE] [P1] Funding state changes did not update `payment_state_changed_at`
Original file: `app/modules/wallet/repository.py:177`

Originally, `update_funding_transfer_state()` changed `payment_state` but left `payment_state_changed_at` unchanged. This has been addressed by updating the timestamp on each state transition.

##### [DONE] [P1] `POST /funding/initiate` ignored the platform `Idempotency-Key` header contract
Original file: `app/modules/wallet/routes.py:73`

Originally, the wallet funding API took `idempotency_key` from the JSON body instead of the `Idempotency-Key` request header, despite the platform contract requiring the header on all financial mutations. This has been addressed by reading the header explicitly and testing that requests without it are rejected.

##### [DONE] [P1] Reusing the same funding idempotency key with a different payload silently replayed the first transfer
Original file: `app/modules/wallet/service.py:128`

Originally, `initiate_funding()` scoped lookup by `(sponsor_id, idempotency_key)` but did not verify that the new request matched the original request parameters, so conflicting reuse returned the old transfer instead of rejecting it. This has been addressed by detecting mismatched request parameters and raising `IdempotencyConflict`.

##### [DONE] [P1] Concurrent duplicate funding requests could fail with a 500 instead of behaving idempotently
Original file: `app/modules/wallet/service.py:128`

Originally, the funding path was a check-then-insert flow with no recovery from a unique-constraint race, so duplicate concurrent requests could surface as 500s. This has been addressed by catching `IntegrityError`, rolling back, and reloading the winning transfer record.

##### [DONE] [P1] `POST /funding/initiate` did not restrict the caller to sponsors
Original file: `app/modules/wallet/routes.py:73`

Originally, the route only depended on `get_current_user`, which allowed any authenticated user with a wallet to initiate funding. This has been addressed by enforcing `require_roles("sponsor")` on the route and adding regression coverage for non-sponsor rejection.

##### [DONE] [P1] Conflicting concurrent reuse of an idempotency key returned a stale success instead of a conflict
Original file: `app/modules/wallet/service.py:171`

Originally, the sequential path raised `IdempotencyConflict` for conflicting key reuse, but the `IntegrityError` race path reloaded and returned the winner without re-checking parameters. This has been addressed by validating the winning record after rollback and raising `IdempotencyConflict` when the concurrent loser used different request parameters. There is now explicit regression coverage for both same-params and different-params race paths.

##### [DONE] [P0] Phase 2 accepted non-positive amounts and could write invalid ledger entries
Original file: `app/modules/wallet/schemas.py:67`

Originally, `source_amount` was unconstrained and the service layer accepted zero or negative amounts, which could create invalid funding transfers and ledger mutations. This has been addressed with schema-level validation for positive funding amounts and service-level guards for both funding and debit operations, plus regression tests for zero and negative values.

#### Open

No findings currently open.

## Phase 3 — Open Banking Sub-Module

No findings recorded yet.

## Phase 4 — Card Module

### 2026-03-19

#### Open

##### [OPEN] [P1] `GET /cards/{card_id}` lets any sponsor read any card
Original file: `app/modules/card/routes.py:105`

The route is documented as sponsor-or-owner access, but the sponsor branch is still a stub: when `current_user.role == "sponsor"` it executes `pass` and falls through to `return card`. That means any authenticated sponsor can fetch any beneficiary card by UUID without proving an active sponsor-beneficiary link.

##### [OPEN] [P1] Card issuance calls the processor before the backend has a durable local record
Original file: `app/modules/card/service.py:69`

`CardService.issue_card()` invokes the external processor first and only then inserts the local `card.cards` row. If the database write or transaction commit fails after the processor has already issued the card, the platform loses track of a real processor-side card and a retry can issue another one. The route also has no idempotency key or reservation step to make that external side effect safe.

##### [OPEN] [P2] Live UP Nigeria mode is wired to a client whose methods still raise `NotImplementedError`
Original file: `app/modules/card/processor/client.py:157`

As soon as `UP_NIGERIA_API_KEY` is configured, `get_processor()` switches from the dev stub to `UPNigeriaClient`, but every lifecycle method in `app/modules/card/processor/up_nigeria.py` still raises `NotImplementedError`. That means issuance, activation, freeze/cancel, and spending-control updates will all 500 immediately in the first non-dev environment that sets the real processor credentials.

## Phase 5 — Transaction Module

### 2026-03-19

#### Open

##### [OPEN] [P1] Phase 5 transaction module is still missing
Original file: `app/main.py:105`

The architecture and plan both say Phase 5 owns `/transactions/*` plus processor authorization and clearing webhooks, but there is still no `app/modules/transaction/` package, no transaction router in `app/main.py`, no transaction migrations, and no `tests/modules/transaction/`. Phase 5 is effectively unimplemented, so POS authorization, clearing, settlement, and disputes do not exist yet.

## Phase 6 — Compliance Module

No findings recorded yet.

## Phase 7 — Vendor Module

No findings recorded yet.

## Phase 8 — Notification Module

No findings recorded yet.

## Phase 9 — Reporting Module

No findings recorded yet.

## Phase 10 — Testing

No findings recorded yet.

## Phase 11 — Security Hardening & Production Readiness

### 2026-03-27

#### Resolved

##### [DONE] [P0] Global idempotency cache could replay one caller's response into another request
Original file: `app/core/middleware.py:46`

Originally, the shared idempotency middleware keyed Redis only by the raw `Idempotency-Key` header and used a read-before-write flow. That allowed cross-route and cross-user response replay, and concurrent duplicates could both enter the handler before either cached a result. This has been addressed by scoping the cache key to method, path, query, authenticated actor, and idempotency key; fingerprinting the request payload; atomically reserving the key with Redis `SET ... NX`; returning `IdempotencyConflict` for conflicting payload reuse; and returning `DuplicateIdempotencyKey` while an identical request is still in flight. Regression coverage now verifies replay, cross-user isolation, cross-route isolation, payload conflict handling, and concurrent duplicate rejection.

#### Open

No findings currently open.
