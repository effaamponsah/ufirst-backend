# Review Findings

Use this file as the running log for review issues. Add new findings under the
phase they belong to so they are easy to pick up during implementation.

## Phase 0 — Project Foundation

No findings recorded yet.

## Phase 1 — Identity Module

### 2026-03-19

#### [P1] Sponsor-only beneficiary endpoints do not enforce sponsor role
File: `app/modules/identity/routes.py:121`

`GET /users/me/beneficiaries` and `DELETE /users/me/beneficiaries/{beneficiary_id}` rely only on `get_current_user`; they never require the caller to be a sponsor. A beneficiary or vendor can call them and get a 200/404 instead of the expected 403, which breaks the endpoint contract and weakens authorization checks. Only the create-link path validates the sponsor role in the service layer.

#### [P0] KYC webhook mutates user status without verifying a signature
File: `app/modules/identity/routes.py:173`

`POST /kyc/webhook` accepts arbitrary JSON and calls `update_kyc_status()` directly, but there is no inbound signature check at all. That lets anyone who can reach the endpoint approve or reject KYC records by forging a webhook payload, which violates the repository rule that every webhook must verify its signature before processing.

#### [P1] Supabase webhook verification fails open when the secret is unset
File: `app/modules/identity/routes.py:37`

The Supabase webhook verifier returns early whenever `SUPABASE_WEBHOOK_SECRET` is blank. In any non-dev deployment with a missing secret, forged `user.created` payloads are accepted and arbitrary `identity.users` rows can be created. This should fail closed outside dev mode.

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

No findings recorded yet.

## Phase 5 — Transaction Module

No findings recorded yet.

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

No findings recorded yet.
