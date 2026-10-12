.PHONY: up down seed seed-reset test lint api-dev web-dev

up:            ## start Postgres, API and web in Docker
	docker compose up --build -d
down:
	docker compose down
seed:          ## create users + fictional demo data
	docker compose exec api python -m app.seed
seed-reset:
	docker compose exec api python -m app.seed --reset
test:          ## API tests (needs a Postgres test database, see README)
	cd api && pytest
lint:
	cd api && ruff check app tests && mypy app
	cd web && npm run lint && npm run typecheck
api-dev:
	cd api && alembic upgrade head && uvicorn app.main:app --reload --port 8000
web-dev:
	cd web && npm run dev
