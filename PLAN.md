# U-FirstSupport Backend — Implementation Plan

> Track status with: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Phase 0 — Project Foundation

Everything every other module depends on. Must be complete before any module work begins.

### 0.1 Project Scaffolding
- [x] Create directory tree: `app/`, `app/modules/`, `app/core/`, `app/jobs/`, `tests/`, `migrations/`
- [x] `pyproject.toml` with all dependencies (fastapi, sqlalchemy, alembic, redis-py, celery, pydantic v2, httpx, mypy, ruff, pytest-asyncio, testcontainers)
- [x] `app/main.py` — FastAPI app factory, router registration, lifespan hooks
- [x] `app/config.py` — `Settings` Pydantic model reading from env (DB URL, Redis URL, Supabase JWKS URL, aggregator keys, etc.)
- [x] `.env.example` with all required variables documented

### 0.2 Database Infrastructure
- [x] SQLAlchemy async engine + session factory (`app/core/database.py`)
- [x] `Base` declarative base with common columns (`id: UUID`, `created_at`, `updated_at`)
- [x] Alembic setup: `alembic.ini`, `env.py` configured for async + per-module migration directories (`migrations/identity/`, `migrations/wallet/`, etc.)
- [x] Multi-schema support: each schema created with `CREATE SCHEMA IF NOT EXISTS <module>` in a base migration

### 0.3 Authentication & Security Middleware
- [x] JWKS cache (`app/core/auth.py`): fetch Supabase JWKS on startup, refresh every hour, verify JWT locally on every request — no per-request Supabase call
- [x] `get_current_user` FastAPI dependency: decode JWT, extract `sub` (user UUID) and `app_metadata.role`
- [x] Role-based access dependency (`require_role(*roles)`)
- [x] Idempotency middleware: parse `Idempotency-Key` header on POST/PUT/PATCH; return cached response for duplicate keys (store in Redis with 24h TTL)
- [x] Global error handler: map exceptions to `{ "error": { "code", "message", "details" } }` format

### 0.4 Core Utilities
- [x] `app/core/pagination.py` — `PaginatedResponse[T]` generic schema + `paginate()` helper
- [x] `app/core/money.py` — `Money(amount: int, currency: str)` dataclass; arithmetic helpers; format validation (reject float)
- [x] `app/core/events.py` — in-process typed event bus: `publish(event)`, `subscribe(EventType, handler)`
- [x] `app/core/exceptions.py` — domain exception hierarchy (`InsufficientBalance`, `KYCRequired`, `IdempotencyConflict`, etc.)
- [x] `app/core/encryption.py` — AES-256-GCM helpers for encrypting PII fields (bank account identifiers, etc.)

### 0.5 Celery & Redis
- [x] `app/jobs/celery_app.py` — Celery app configured with Redis broker, three queues: `critical`, `default`, `bulk`
- [x] `app/jobs/beat_schedule.py` — Celery beat schedule (placeholder, populated by each module)
- [x] Redis client singleton (`app/core/redis.py`)

---

## Phase 1 — Identity Module

Owns users, KYC, and sponsor↔beneficiary relationships. All other modules resolve user data through `IdentityService`.

### 1.1 Database Schema (`identity.*`)
- [x] `identity.users` — `id` (UUID, matches Supabase `auth.users.id`), `email`, `phone`, `full_name`, `role`, `kyc_status`, `created_at`
- [x] `identity.kyc_submissions` — documents, provider reference, status, reviewer notes
- [x] `identity.sponsor_beneficiary_links` — `sponsor_id`, `beneficiary_id`, `status` (ACTIVE/SUSPENDED), `created_at`
- [x] Migration: `migrations/versions/20260319_0001_identity_initial_schema.py`

### 1.2 Service Interface (`app/modules/identity/service.py`)
- [x] `get_user(user_id: UUID) -> UserProfile`
- [x] `get_link(sponsor_id: UUID, beneficiary_id: UUID) -> SponsorBeneficiaryLink`
- [x] `list_beneficiaries(sponsor_id: UUID) -> list[UserProfile]`
- [x] `verify_sponsor_beneficiary_link(sponsor_id, beneficiary_id)` — raises if no active link
- [x] `update_kyc_status(user_id, status, provider_ref)`

### 1.3 Routes (`/api/v1/`)
- [x] `POST /auth/webhook/user-created` — Supabase `user.created` webhook; verify Supabase webhook signature; create `identity.users` record; publish `UserCreated` event
- [x] `GET /users/me` — current user profile
- [x] `GET /users/{user_id}` — admin/ops only
- [x] `GET /users/me/beneficiaries` — sponsor: list linked beneficiaries
- [x] `POST /users/me/beneficiaries/{beneficiary_id}` — create sponsor↔beneficiary link
- [x] `DELETE /users/me/beneficiaries/{beneficiary_id}` — deactivate link
- [x] `POST /kyc/submit` — submit KYC documents (multipart upload to object storage, store reference)
- [x] `GET /kyc/status` — current KYC status for authenticated user
- [x] `POST /kyc/webhook` — KYC provider webhook (status update); verify signature

### 1.4 Events
- [x] `UserCreated(user_id, role, email)`
- [x] `KYCStatusChanged(user_id, old_status, new_status)`
- [x] `SponsorBeneficiaryLinked(sponsor_id, beneficiary_id)`

---

## Phase 2 — Wallet Module (Core)

Owns wallets and the append-only ledger. The financial core of the system.

### 2.1 Database Schema (`wallet.*`)
- [x] `wallet.wallets` — `id`, `owner_id` (user UUID), `currency`, `available_balance` (int), `reserved_balance` (int), `status`
- [x] `wallet.ledger_entries` — `id`, `wallet_id`, `entry_type` (DEBIT/CREDIT), `amount` (int), `currency`, `reference_type`, `reference_id`, `description`, `created_at` — **NO `updated_at`, no UPDATE, no DELETE**
- [x] `wallet.funding_transfers` — `id`, `sponsor_id`, `wallet_id`, `payment_method` (ENUM), `payment_state` (ENUM), `payment_state_changed_at`, `source_amount`, `source_currency`, `fx_rate`, `fx_rate_locked_until`, `dest_amount`, `dest_currency`, `fee_amount`, `idempotency_key`, `external_payment_ref`, `created_at`
- [x] Migration: `migrations/versions/20260319_0002_wallet_initial_schema.py`

### 2.2 Service Interface (`app/modules/wallet/service.py`)
- [x] `get_wallet(wallet_id: UUID) -> WalletResponse`
- [x] `get_wallet_by_owner(owner_id: UUID) -> WalletResponse`
- [x] `create_wallet(owner_id: UUID, currency: str) -> WalletResponse`
- [x] `credit_from_funding(transfer_id: UUID)` — SERIALIZABLE transaction: ledger credit + balance update + state → COMPLETED
- [x] `debit_wallet(wallet_id, amount, reference_type, reference_id)` — for card authorizations
- [x] `get_ledger(wallet_id, limit, offset) -> list[LedgerEntryResponse]`
- [x] `initiate_funding(...)` — idempotency check, wallet validation, create FundingTransfer
- [x] `advance_funding_state(transfer_id, new_state)` — enforces valid state transitions

### 2.3 Routes (`/api/v1/`)
- [x] `GET /wallets/me` — own wallet balance
- [x] `GET /wallets/{wallet_id}` — admin/ops/compliance only
- [x] `GET /wallets/me/ledger` — paginated ledger entries
- [x] `POST /funding/initiate` — create funding transfer
- [x] `GET /funding/{transfer_id}` — get transfer status
- [x] `PATCH /funding/{transfer_id}/state` — admin/ops: advance state machine
- [x] `POST /funding/{transfer_id}/complete` — admin/ops: credit wallet (for testing/manual ops)

### 2.4 Events
- [x] `WalletCreated(wallet_id, owner_id, currency)`
- [x] `WalletFunded(wallet_id, amount, currency, funding_transfer_id)`
- [x] `WalletDebited(wallet_id, amount, currency, reference_type, reference_id)`

---

## Phase 3 — Open Banking Sub-Module (within Wallet)

The primary funding mechanism. Lives entirely within `app/modules/wallet/openbanking/`.

### 3.1 Aggregator Client (`openbanking/client.py`)
- [x] Abstract `PaymentAdapter` interface: `initiate(...)`, `check_status(payment_id)`, `verify_webhook(body, headers)`, `parse_webhook(body)`, `refund(payment_id, amount)`
- [x] `TrueLayerClient(PaymentAdapter)` — all TrueLayer API calls, auth (client credentials OAuth), retries with exponential backoff, error mapping to internal exceptions
- [x] `OpenBankingMapper` — maps TrueLayer response fields to internal models; isolates any future provider switch to this file only
- [x] `StripeClient(PaymentAdapter)` — card fallback adapter (Phase 3.8)
- [x] `DevPaymentAdapter` — no-op stub returned by factory when credentials are absent
- [x] Aggregator resolved at runtime from `settings.OPENBANKING_PROVIDER` via `get_adapter()` factory

### 3.2 Database Schema Additions (`wallet.*`)
- [x] `wallet.sponsor_bank_connections` — all columns; `account_identifier_encrypted` as `BYTEA` (AES-256-GCM)
- [x] `wallet.open_banking_payments` — links FundingTransfer to aggregator payment record
- [x] `wallet.open_banking_webhooks_log` — raw payload audit log (`id`, `aggregator`, `event_type`, `payload` JSONB, `signature_valid`, `processed_at`, `processing_error`)
- [x] `wallet.card_payments` — Stripe `payment_intent_id`, card last4, card brand, fee charged
- [x] Migration: `migrations/versions/20260319_0006_wallet_open_banking.py`
- [x] Migration: `migrations/versions/20260319_0007_wallet_card_payments.py`

### 3.3 State Machine (`openbanking/payments.py`)
- [x] `PaymentInitiationService.initiate_payment(...)` enforces valid transitions:
  - `INITIATED → AWAITING_AUTHORIZATION | FAILED`
  - `AWAITING_AUTHORIZATION → AUTHORIZING | EXPIRED | CANCELLED`
  - `AUTHORIZING → AWAITING_SETTLEMENT | FAILED`
  - `AWAITING_SETTLEMENT → COMPLETED | FAILED`
  - Terminal states (`COMPLETED`, `FAILED`, `EXPIRED`, `CANCELLED`) raise on any transition attempt
- [x] Full initiation flow: link verification → compliance stub → FX rate lock → create FundingTransfer → call adapter → create OB/card payment record → advance to AWAITING_AUTHORIZATION → publish FundingInitiated

### 3.4 Webhook Handler (`openbanking/webhooks.py`)
- [x] `POST /api/v1/webhooks/openbanking/payment-status` — verify HMAC-SHA256 signature → replay prevention → log to webhooks_log → dispatch to Celery critical queue → return 200
- [x] `POST /api/v1/webhooks/openbanking/connect-callback` — same pattern, default queue
- [x] `POST /api/v1/webhooks/stripe/payment-status` — Stripe-Signature verification → log → dispatch
- [x] Celery task `process_payment_webhook(payload, aggregator)` — handles payment_executed/failed/pending; advances state machine; credits wallet on executed

### 3.5 Bank Connection Flow (`openbanking/connections.py`)
- [x] `BankConnectionService.create_connection_session(sponsor_id) -> StartBankLinkResponse`
- [x] `BankConnectionService.complete_connection(sponsor_id, code)` — stores AES-256-GCM encrypted account identifier
- [x] `BankConnectionService.revoke_connection(connection_id, sponsor_id)` — best-effort aggregator revoke, marks REVOKED locally
- [x] `GET /api/v1/funding/banks` — list sponsor's active linked bank accounts
- [x] `POST /api/v1/funding/banks/link` — start bank link session
- [x] `POST /api/v1/funding/banks/complete` — complete bank link after sponsor redirect
- [x] `DELETE /api/v1/funding/banks/{connection_id}` — revoke connection

### 3.6 Funding Endpoints
- [x] `POST /api/v1/funding/initiate` — `Idempotency-Key` required; runs initiation flow; returns `{ auth_link, funding_transfer_id, payment_state }`
- [x] `GET /api/v1/funding/{transfer_id}/status` — polled by frontend; returns current `payment_state`
- [x] `POST /api/v1/funding/{transfer_id}/cancel` — cancel if state is `AWAITING_AUTHORIZATION`

### 3.7 Safety Net & Expiry (Celery Beat Jobs)
- [x] **Poller** (every 5 min): query `AWAITING_SETTLEMENT` transfers with no webhook; call `aggregator.check_status()`
- [x] **Expiry** (every 1 min): query `AWAITING_AUTHORIZATION` where `payment_state_changed_at < now - 15 min`; transition to `EXPIRED`; publish `FundingAuthorizationExpired`
- [x] **Consent expiry warning** (daily 08:00 UTC): query `sponsor_bank_connections` where `consent_expires_at < now + 7 days`; publish `BankConsentExpiring`
- [ ] **FX rate expiry**: re-quote rate at settlement time if `fx_rate_locked_until` has passed; log the difference

### 3.8 Card Payment Adapter (Fallback)
- [x] `StripeClient(PaymentAdapter)` — Stripe PaymentIntent for debit/credit card funding
- [x] `wallet.card_payments` table — Stripe `payment_intent_id`, card last4, card brand, fee charged
- [x] `POST /api/v1/webhooks/stripe/payment-status` — verify Stripe webhook signature; dispatch to `critical` queue
- [x] Routing logic in `PaymentInitiationService`: OPEN_BANKING → TrueLayerClient; CARD → StripeClient; ACH/MOBILE_MONEY stubs

---

## Phase 4 — Card Module

Owns card lifecycle and processor token management. Never stores raw PANs.

### 4.1 Database Schema (`card.*`)
- [x] `card.cards` — `id`, `wallet_id`, `owner_id` (beneficiary), `processor_token` (NOT a PAN), `card_program_id`, `status` (PENDING/ACTIVE/FROZEN/CANCELLED), `spending_controls` (JSONB), `issued_at`, `expires_at`
- [x] `card.card_events` — append-only audit log of every card status change
- [x] Migration: `migrations/versions/20260319_0008_card_initial_schema.py`

### 4.2 Service Interface (`app/modules/card/service.py`)
- [x] `CardService.issue_card(wallet_id, beneficiary_id, issued_by, spending_controls) -> CardResponse` — calls processor; card starts PENDING; never logs/returns raw PAN
- [x] `CardService.activate_card(card_id, actor_id)` — PENDING → ACTIVE; called when UP Nigeria confirms physical card dispatched
- [x] `CardService.get_card(card_id) -> CardResponse`
- [x] `CardService.freeze_card(card_id, actor_id, reason)`
- [x] `CardService.unfreeze_card(card_id, actor_id)`
- [x] `CardService.cancel_card(card_id, actor_id, reason)`
- [x] `CardService.update_spending_controls(card_id, controls, actor_id)`
- [x] `CardService.get_card_for_wallet(wallet_id) -> CardResponse | None`

### 4.3 Routes (`/api/v1/cards/`)
- [x] `POST /cards/` — sponsor issues card for linked beneficiary; checks KYC status via `IdentityService`; card starts PENDING
- [x] `POST /cards/{card_id}/activate` — ops/admin: activate PENDING card (UP Nigeria dispatch confirmation)
- [x] `GET /cards/{card_id}` — sponsor or beneficiary owner
- [x] `POST /cards/{card_id}/freeze`
- [x] `POST /cards/{card_id}/unfreeze`
- [x] `DELETE /cards/{card_id}` — cancel card
- [x] `PUT /cards/{card_id}/controls` — update spending controls (sponsor only)

### 4.4 Processor Client (`card/processor/`)
- [x] `CardProcessorClient` abstract interface: `issue_card(...)`, `activate_card(...)`, `update_card_status(...)`, `update_spending_controls(...)`
- [x] `DevCardProcessorClient` — no-op stub; used when `UP_NIGERIA_API_KEY` is not set
- [x] `UPNigeriaClient` skeleton (`card/processor/up_nigeria.py`) — clearly marked integration points; wired in automatically when `UP_NIGERIA_API_KEY` is configured
- [x] `get_processor()` factory — returns `UPNigeriaClient` when credentials present, `DevCardProcessorClient` otherwise

---

## Phase 5 — Transaction Module

Owns authorization, clearing, settlement, and disputes for POS card transactions.

### 5.1 Database Schema (`transaction.*`)
- [ ] `transaction.authorizations` — `id`, `card_id`, `wallet_id`, `merchant_name`, `merchant_category_code`, `amount`, `currency`, `status` (ENUM: AUTHORIZED/DECLINED/REVERSED), `processor_auth_ref`, `authorized_at`
- [ ] `transaction.clearings` — `id`, `authorization_id`, `cleared_amount`, `cleared_currency`, `cleared_at`, `processor_clearing_ref`
- [ ] `transaction.settlements` — `id`, `vendor_id`, `clearing_ids` (array), `total_amount`, `currency`, `status`, `settled_at`
- [ ] `transaction.disputes` — `id`, `authorization_id`, `reason`, `status`, `opened_at`, `resolved_at`, `resolution`
- [ ] Migration: `migrations/transaction/001_initial_schema.py`

### 5.2 Service Interface
- [ ] `TransactionService.authorize(auth_request: AuthorizationRequest) -> AuthorizationDecision`
  - Spending control check (category, daily limit, merchant allowlist from card controls)
  - Compliance screen (real-time)
  - Reserve balance (`WalletService.reserve()`)
  - Return APPROVED or DECLINED with reason code
- [ ] `TransactionService.process_clearing(clearing_data) -> Clearing`
  - Match to authorization, confirm amount
  - Convert reserved balance to settled debit (create ledger entry via WalletService)
- [ ] `TransactionService.process_reversal(auth_ref) -> void`
  - Release reserved balance
- [ ] `TransactionService.open_dispute(authorization_id, reason) -> Dispute`
- [ ] `TransactionService.list_transactions(wallet_id, page, per_page) -> PaginatedResponse`

### 5.3 Routes (`/api/v1/transactions/`)
- [ ] `GET /transactions/` — sponsor sees all beneficiary transactions; beneficiary sees own
- [ ] `GET /transactions/{transaction_id}` — detail view
- [ ] `POST /transactions/{transaction_id}/dispute`
- [ ] `POST /webhooks/card-processor/authorization` — real-time auth hook; synchronous response required; **must respond within 2 seconds**
- [ ] `POST /webhooks/card-processor/clearing`
- [ ] `POST /webhooks/card-processor/reversal`

---

## Phase 6 — Compliance Module

AML screening, rules engine, SARs, and alerts. Admin-only API surface.

### 6.1 Database Schema (`compliance.*`)
- [ ] `compliance.screening_results` — `id`, `entity_type`, `entity_id`, `screen_type` (AML/SANCTIONS/PEP), `result` (PASS/FAIL/REVIEW), `provider_ref`, `screened_at`
- [ ] `compliance.aml_rules` — configurable rules: velocity limits, amount thresholds, geographic rules, category rules
- [ ] `compliance.alerts` — `id`, `alert_type`, `entity_id`, `entity_type`, `severity`, `status` (OPEN/INVESTIGATING/CLOSED), `created_at`, `assigned_to`
- [ ] `compliance.sars` — Suspicious Activity Reports: `id`, `alert_id`, `narrative`, `submitted_at`, `submission_ref`
- [ ] `compliance.reconciliation_breaks` — `id`, `break_type`, `detected_at`, `amount_difference`, `status`, `resolved_at`, `resolution_notes`
- [ ] Migration: `migrations/compliance/001_initial_schema.py`

### 6.2 Service Interface
- [ ] `ComplianceService.screen_funding(sponsor_id, amount, currency) -> ScreeningResult`
  - Velocity check (daily/monthly limits per sponsor)
  - Sanctions screening (call external provider or in-house list)
  - AML rules evaluation
  - Returns PASS (proceed) or FAIL (reject with reason code)
- [ ] `ComplianceService.screen_transaction(card_id, merchant, amount) -> ScreeningResult`
- [ ] `ComplianceService.create_alert(alert_type, entity_id, severity) -> Alert`
- [ ] `ComplianceService.file_sar(alert_id, narrative) -> SAR`

### 6.3 Routes (`/api/v1/compliance/`) — all require `compliance_officer` or `admin` role
- [ ] `GET /compliance/alerts` — paginated alert list with filters
- [ ] `GET /compliance/alerts/{alert_id}`
- [ ] `POST /compliance/alerts/{alert_id}/assign`
- [ ] `POST /compliance/alerts/{alert_id}/close`
- [ ] `POST /compliance/sars` — file SAR from alert
- [ ] `GET /compliance/sars`
- [ ] `GET /compliance/reconciliation-breaks`
- [ ] `POST /compliance/reconciliation-breaks/{id}/resolve`

---

## Phase 7 — Vendor Module

Vendor onboarding, profiles, and settlement processing.

### 7.1 Database Schema (`vendor.*`)
- [ ] `vendor.vendors` — `id`, `legal_name`, `trading_name`, `registration_number`, `status` (PENDING/ACTIVE/SUSPENDED), `settlement_currency`, `settlement_bank_account_encrypted`
- [ ] `vendor.vendor_locations` — `id`, `vendor_id`, `address`, `geolocation`, `terminal_ids` (array), `status`
- [ ] `vendor.settlement_batches` — `id`, `vendor_id`, `period_start`, `period_end`, `gross_amount`, `fee_amount`, `net_amount`, `status` (PENDING/PROCESSING/SETTLED/FAILED), `settled_at`
- [ ] Migration: `migrations/vendor/001_initial_schema.py`

### 7.2 Service Interface
- [ ] `VendorService.get_vendor(vendor_id) -> Vendor`
- [ ] `VendorService.get_vendor_for_terminal(terminal_id) -> Vendor` — used by transaction auth
- [ ] `VendorService.create_settlement_batch(vendor_id, period) -> SettlementBatch`
- [ ] `VendorService.process_settlement(batch_id)` — aggregate cleared transactions; trigger bank transfer; Celery task

### 7.3 Routes
- [ ] `POST /vendors/` — ops: onboard new vendor
- [ ] `GET /vendors/{vendor_id}`
- [ ] `PUT /vendors/{vendor_id}`
- [ ] `POST /vendors/{vendor_id}/suspend` / `activate`
- [ ] `GET /vendors/{vendor_id}/locations`
- [ ] `POST /vendors/{vendor_id}/locations`
- [ ] `GET /settlements/` — ops: list settlement batches
- [ ] `GET /settlements/{batch_id}`
- [ ] `POST /settlements/{batch_id}/process` — manual trigger for ops

---

## Phase 8 — Notification Module

Internal only — no public API. Purely event-driven.

### 8.1 Database Schema (`notification.*`)
- [ ] `notification.notification_log` — `id`, `user_id`, `channel` (SMS/PUSH/EMAIL), `template_key`, `payload` JSONB, `status`, `external_ref`, `sent_at`, `failed_at`, `error`
- [ ] Migration: `migrations/notification/001_initial_schema.py`

### 8.2 Channels
- [ ] `SmsProvider` client class (Twilio or Africa's Talking for home-country numbers)
- [ ] `PushProvider` client class (Firebase Cloud Messaging)
- [ ] `EmailProvider` client class (SendGrid or Postmark)

### 8.3 Templates — one per notification type
- [ ] Funding: `COMPLETE_BANK_AUTH`, `PAYMENT_PROCESSING`, `WALLET_FUNDED`, `PAYMENT_FAILED`, `AUTH_EXPIRED`
- [ ] Card: `CARD_ISSUED`, `CARD_ACTIVATED`, `CARD_FROZEN`, `CARD_TRANSACTION` (per-transaction receipt)
- [ ] KYC: `KYC_SUBMITTED`, `KYC_APPROVED`, `KYC_REJECTED`
- [ ] Bank connections: `BANK_LINKED`, `BANK_CONSENT_EXPIRING`, `BANK_DISCONNECTED`
- [ ] Compliance: `ACCOUNT_SUSPENDED`, `SAR_FILED` (ops only)

### 8.4 Event Subscriptions
- [ ] Subscribe to: `WalletFunded`, `FundingInitiated`, `FundingAuthorizationExpired`, `FundingFailed`, `FundingPaymentReceived`
- [ ] Subscribe to: `CardIssued`, `CardActivated`, `CardFrozen`, `CardTransactionAuthorized`, `CardTransactionDeclined`
- [ ] Subscribe to: `KYCStatusChanged`, `BankConnectionCreated`, `BankConsentExpiring`

---

## Phase 9 — Reporting Module

Analytics and three-way reconciliation. Admin/ops API.

### 9.1 Database Schema (`reporting.*`)
- [ ] `reporting.reconciliation_runs` — `id`, `date`, `status` (PASSED/FAILED), `ledger_total`, `aggregator_total`, `bank_total`, `discrepancy`, `run_at`
- [ ] `reporting.daily_snapshots` — pre-aggregated daily stats per wallet/vendor for reporting queries
- [ ] Migration: `migrations/reporting/001_initial_schema.py`

### 9.2 Three-Way Reconciliation (Celery Beat — 03:00 UTC daily)
- [ ] **Step 1** — Ledger vs Aggregator: for each `COMPLETED` funding transfer, verify aggregator also reports `EXECUTED`
- [ ] **Step 2** — Aggregator vs Bank statement: match aggregator `EXECUTED` payments to credits on escrow bank statement (parsed MT940/CAMT.053 or via bank AIS API)
- [ ] **Step 3** — Bank vs Ledger totals: sum all incoming credits on bank statement vs sum of all `COMPLETED` funding transfers for the day (tolerance: ±£0.01)
- [ ] **Step 4** — Orphan detection: bank credits with no matching `funding_transfer`
- [ ] **Step 5** — Generate report; if mismatches → create `compliance.reconciliation_break` + PagerDuty/Slack alert to ops

### 9.3 Routes (`/api/v1/reports/`) — ops/admin only
- [ ] `GET /reports/reconciliation` — list reconciliation runs with pass/fail status
- [ ] `GET /reports/reconciliation/{date}` — detail view with line-by-line breakdown
- [ ] `GET /reports/funding` — funding volume by method, status, currency; date range filter
- [ ] `GET /reports/transactions` — transaction volume by vendor, category; date range
- [ ] `GET /reports/settlements` — vendor settlement summary

---

## Phase 10 — Testing

### 10.1 Infrastructure
- [x] `conftest.py` — `pytest-asyncio` setup; real PostgreSQL + Redis fixtures; async DB session; dev-mode auth bypass
- [x] `TestClient` with injected auth token (dev-mode `Bearer dev:<user_id>:<role>` scheme)
- [ ] Async database rollback isolation per test (currently uses table truncation; switch to per-test transaction rollback)

### 10.2 Unit Tests
- [x] Funding state machine: valid transitions tested; invalid transitions raise `InvalidStateTransition`
- [x] Card state machine: valid transitions (PENDING→ACTIVE→FROZEN→ACTIVE→CANCELLED); invalid transitions raise
- [x] Idempotency: duplicate `Idempotency-Key` returns cached response; conflict on different params raises
- [x] Webhook signature verification: valid, invalid, missing header (identity KYC webhook)
- [x] Ledger balance consistency: `sum(credits) - sum(debits) == available_balance` asserted in every financial test
- [ ] FX rate locking: expiry detection, re-quote on late settlement
- [ ] Money type: rejects float, correct minor-unit arithmetic

### 10.3 Integration Tests (per module)
- [x] `tests/modules/identity/test_user_creation.py` — lazy provisioning, profile update, beneficiary creation
- [x] `tests/modules/identity/test_beneficiary_links.py` — link creation, list, removal
- [x] `tests/modules/identity/test_webhook_security.py` — KYC webhook signature verification
- [x] `tests/modules/wallet/test_fund_wallet.py` — full funding happy path, idempotency, state machine, concurrent race
- [x] `tests/modules/wallet/test_wallet_creation.py` — wallet creation, auth guards
- [x] `tests/modules/card/test_card_lifecycle.py` — issue, activate, freeze, unfreeze, cancel, spending controls, KYC gate, unlinked sponsor blocked
- [ ] `tests/modules/wallet/test_webhook_idempotency.py` — duplicate webhook, out-of-order, replay prevention
- [ ] `tests/modules/wallet/test_state_machine.py` — every branch of the funding state machine with real DB
- [ ] `tests/modules/transaction/test_authorization.py` — authorize, clear, reverse
- [ ] `tests/modules/compliance/test_screening.py` — velocity rules, sanctions match, AML flag

### 10.4 Reconciliation Tests
- [ ] Seed: 10 completed funding transfers; mock aggregator reports all 10 EXECUTED; mock bank statement matches all 10 → run should PASS
- [ ] Seed: introduce deliberate mismatch (aggregator missing one, bank has extra credit, FX rounding diff) → run should FAIL and create `reconciliation_break`

### 10.5 Webhook Reliability Tests
- [ ] Duplicate webhook delivery → idempotency prevents double credit
- [ ] Webhook arrives before DB record created (race) → retry handles it
- [ ] Webhook endpoint returns 500 → aggregator retries → eventually processes
- [ ] Poller catches a payment whose webhook was never delivered

---

## Phase 11 — Security Hardening & Production Readiness

### 11.1 Security
- [ ] Penetration test on webhook endpoints (unsigned requests, replayed signatures, payload injection)
- [ ] Audit: confirm no raw PANs in logs, DB, or API responses anywhere in codebase
- [ ] IP allowlisting: verify aggregator source IPs at application level (if aggregator publishes IP ranges)
- [ ] PSD2 consent management audit: all AIS consent expiry tracked; re-consent prompts working; revocation works end-to-end
- [ ] Secrets rotation procedure documented: aggregator keys, DB credentials, encryption keys

### 11.2 Observability
- [ ] Structured JSON logging on all requests (request ID, user ID, latency, status)
- [ ] Key metrics exposed (Prometheus or Render metrics): webhook processing latency, funding conversion rate, reconciliation pass rate
- [ ] Alerting: reconciliation break → PagerDuty; webhook processing error spike → Slack; funding FAILED rate > 5% → alert

### 11.3 Load Testing
- [ ] Simulate 100 concurrent `POST /funding/initiate` requests with the same idempotency key (only one should proceed)
- [ ] Simulate 100 concurrent different funding requests (all should complete without ledger corruption)
- [ ] Webhook flood: 1000 webhook deliveries in 60 seconds, verify deduplication and processing integrity

### 11.4 Production Deployment (Render)
- [ ] Render services: `api` (web), `worker` (Celery worker), `beat` (Celery beat), `migrate` (one-off pre-deploy migration runner)
- [ ] Environment variable configuration in Render dashboard for prod secrets
- [ ] Feature flag: `OPEN_BANKING_ENABLED=false` initially; roll out to beta sponsors first
- [ ] Staging environment: connected to aggregator sandbox; separate DB and Redis
- [ ] Health check endpoint: `GET /health` — checks DB connectivity, Redis connectivity, JWKS cache freshness

---

## Dependency Order

```
Phase 0 (Foundation)
    └── Phase 1 (Identity)
            ├── Phase 2 (Wallet Core)
            │       └── Phase 3 (Open Banking)
            │               └── Phase 5 (Transactions) ← also needs Phase 4
            ├── Phase 4 (Card)
            │       └── Phase 5 (Transactions)
            ├── Phase 6 (Compliance) ← called by Phase 3 and Phase 5
            ├── Phase 7 (Vendor) ← called by Phase 5 for settlement
            ├── Phase 8 (Notifications) ← subscribes to events from all modules
            └── Phase 9 (Reporting) ← reads from all modules
                        └── Phase 10 (Testing)
                                └── Phase 11 (Hardening)
```

---

## Open Banking Sprint Schedule (from Addendum §10.1)

| Sprint | Dates | Focus |
|--------|-------|-------|
| Sprint 1 | Weeks 1–2 | Aggregator account + sandbox · `OpenBankingClient` scaffold · DB schema · State machine + unit tests |
| Sprint 2 | Weeks 3–4 | Bank connection flow · Payment initiation happy path · Webhook handler · Aggregator sandbox integration tests |
| Sprint 3 | Weeks 5–6 | Failure states (expired/cancelled/rejected) · Polling safety net · FX rate locking · Notification templates · Compliance screening integration |
| Sprint 4 | Weeks 7–8 | Mobile app UI (frontend) · Card payment fallback (Stripe adapter) |
| Sprint 5 | Weeks 9–10 | Three-way reconciliation job · Admin funding dashboard · E2E staging tests · Load testing |
| Sprint 6 | Weeks 11–12 | Security hardening · Pentest · Production deploy with feature flag · Beta go-live |

## Success Metrics (go-live targets)

| Metric | Target |
|--------|--------|
| Funding conversion rate | > 80% of INITIATED → COMPLETED |
| Time to credit (UK Faster Payments) | < 30 seconds from bank auth |
| Time to credit (SEPA Instant) | < 60 seconds |
| Webhook reliability | < 0.1% require polling fallback |
| Daily reconciliation auto-pass rate | 100% |
| Open banking adoption (UK/EU sponsors) | > 70% of funding volume |
| Average cost per funded transaction | < £0.25 |
