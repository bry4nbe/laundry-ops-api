# AGENTS.md — laundry-ops-api

Operational management system for a laundry business in Lima, Peru (single-tenant).
Backend API. Frontend lives in a separate repo (laundry-ops-web, React SPA on Vercel).

**Respond to the user in Spanish.** Code, identifiers, and comments stay in English.

---

## Behavioral Guidelines

### 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.

### 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked. No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- This project explicitly prefers pragmatic solutions over over-engineering.
- A service layer (services.py) exists ONLY where business logic justifies it (ADR-002).
  Simple CRUD goes directly in serializers/views.

### 3. Surgical Changes
Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor what isn't broken. Match existing style.
- Remove imports/variables YOUR changes made unused; leave pre-existing dead code (mention it).
- Every changed line should trace directly to the request.

### 4. Verifiable Goals (testing is selective — ADR-015)
Define success criteria before implementing.
- Tests are substantial where money/state lives (orders, payments), minimal elsewhere (users, clients).
- Do NOT pursue universal coverage. Effort follows risk, not coverage for its own sake.
- For CRUD without business logic, manual verification + one or two targeted tests suffice.
- State a brief plan for multi-step tasks: step → verify check.

---

## Workflow Rules

- **"SI" rule:** Do NOT generate files or code without explicit confirmation ("SI") or a direct ask. Plans and explanations first.
- **Division of labor:** Codex Chat (Codex.ai project) handles refinement, decisions, research. Codex handles repo execution. Architecture decisions are made in Chat, executed here.
- **Source of truth for decisions:** numbered ADRs live in `../contexto/decisions-lops.md`, with working context in `../contexto/context-lops.md`. Product and technical documentation live in `../laundry-ops-architecture/`. When a decision seems unclear, consult the numbered ADRs first.
- **Code formatting:** never insert manual line breaks mid-sentence in code blocks or generated prompts. One continuous line per bullet/paragraph regardless of length.
- **Git workflow:** keep `main` stable and develop changes on short-lived `develop/<change>` branches created from updated `main`. `develop/` is a branch prefix, not a permanent integration branch; do not use `codex/` in this repo. Use Conventional Commits, verify the feature branch, and merge locally into `main` with `--no-ff` to preserve individual commits. Verify the integrated result before pushing `main`; never push failed validations or unresolved conflicts. Pull requests are optional for additional review. Do not develop features directly on `main`.

---

## Stack

- Python 3.12.10 (frozen minor, floating patches — ADR-024)
- Django 5.2 LTS (anchored to LTS, not latest)
- PostgreSQL 18
- DRF 3.16.1, djangorestframework-simplejwt 5.5.1, django-cors-headers, python-decouple
- Ruff (linter + formatter, target py312), Pytest + pytest-django, factory_boy

## Architecture

Seven apps under apps/: users, clients, catalog, orders, payments, dashboard (read-only, no models or migrations), expenses.
- Migration dependency chain: catalog ← orders (OrderItem.catalog_item FK) ← payments. Renaming/altering catalog or orders models requires regenerating migrations for dependents too.
- Deleting/renaming a migration file that a dependent app references by name (e.g. payments' `('orders', '0001_initial')`) breaks `makemigrations` for the whole project with `NodeNotFoundError`, even for unrelated apps — delete and regenerate both apps' migrations together, keeping the same file name.
- Test convention: `apps/<app>/tests/test_<app>.py` (package, not the default `tests.py`) — delete the placeholder `tests.py` when adding real tests to a new app.
- Custom User model (AbstractUser), username-based auth, email optional (ADR-003). It drops `first_name`/`last_name` — any `ModelAdmin` for `User` must define explicit `fieldsets`/`add_fieldsets` (not inherit `UserAdmin`'s defaults, which reference those fields and break the admin form).
- JWT in response body, not httpOnly cookies (correct for SPA on separate domain).
- User management via Django Admin only — no dedicated API (ADR-005).
- Users are deactivated, never deleted (PROTECT on created_by FKs, ADR-006).
- order_number generated in services.py inside transaction.atomic() as ORD-{id:05d} (ADR-008).
- DEFAULT_AUTO_FIELD = BigAutoField.

## Commands (PowerShell on Windows 10)

```powershell
# Activate venv (REQUIRED — the terminal does not inherit activation)
.venv\Scripts\Activate.ps1

# Run / migrate
python manage.py runserver
python manage.py migrate
python manage.py makemigrations <app>

# Test & lint
pytest
pytest --co -q          # collect only, verify config
ruff check .
ruff check . --fix

# Reset dev DB from scratch (dev only — never in prod)
python manage.py shell -c "from django.db import connection; connection.cursor().execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')"
python manage.py migrate
```

## Gotchas (Windows / PowerShell)

- `where pip` does NOT work in pwsh (`where` is an alias of Where-Object). Use `Get-Command pip` or `python -m pip`.
- The venv does not auto-activate in new terminals — activate explicitly before any pip/manage.py command.
- curl in pwsh mangles JSON quotes — use Invoke-RestMethod or Postman for API testing.
- After regenerating migrations, the dev DB may be out of sync — run migrate (or drop/recreate schema) before createsuperuser.
- createsuperuser sets Django flags (is_superuser) but NOT the custom `role` field — set role=ADMIN manually in Django Admin.
- `manage.py shell -c "..."` with quotes/multi-line Python breaks in PowerShell — write a temp .py file and run `Get-Content script.py | python manage.py shell` instead.
- Manually verifying views with Django's test `Client` raises `DisallowedHost` for `testserver` — append it to `settings.ALLOWED_HOSTS` at runtime in the verification script (don't edit settings.py).
- Django DecimalField assigned a string literal at `.create()`/`__init__` time (e.g. `CatalogItem.objects.create(base_price="5.00")`) stays a raw `str` in memory until reloaded from DB — wrap with `Decimal(...)` before arithmetic, don't assume model fields are already `Decimal`.
- `rest_framework.pagination.PageNumberPagination` is a no-op without `page_size` — this repo has no global `PAGE_SIZE` in `REST_FRAMEWORK` settings, so paginated views need a subclass with `page_size` set, or they'll silently return a bare list instead of `{"count","results",...}`.
