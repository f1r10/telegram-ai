UV := uv run --no-sync

.PHONY: up down logs test test-integration lint typecheck fmt migrate seed

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	$(UV) pytest -m "not integration"

test-integration:
	$(UV) pytest -m integration

lint:
	$(UV) ruff check src tests
	$(UV) ruff format --check src tests

typecheck:
	$(UV) pyright

fmt:
	$(UV) ruff check --fix src tests
	$(UV) ruff format src tests

migrate:
	$(UV) alembic upgrade head

# ASSUMPTION: no seed script exists yet; see docs/OPEN-QUESTIONS.md Q14.
seed:
	@echo "No seed script yet (OPEN-QUESTIONS Q14)." && exit 1
