.PHONY: build up down logs test clean migrate shell health check-llm trigger-reaper

## Build all Docker images
build:
	docker compose build

## Start all services in detached mode (including beat scheduler)
up:
	docker compose up -d
	@echo "✅ Services started:"
	@echo "   API:    http://localhost:8000/docs"
	@echo "   Flower: http://localhost:5555"
	@echo "   Health: http://localhost:8000/health"

## Stop all services
down:
	docker compose down

## View live logs
logs:
	docker compose logs -f

## View API logs only
logs-api:
	docker compose logs -f api

## View worker logs only
logs-worker:
	docker compose logs -f worker

## Run test suite inside api container
test:
	docker compose exec api pytest tests/ -v --tb=short

## Run only unit tests
test-unit:
	docker compose exec api pytest tests/unit/ -v --tb=short

## Run only integration tests
test-integration:
	docker compose exec api pytest tests/integration/ -v --tb=short

## Run Alembic migrations inside api container
migrate:
	docker compose exec api alembic upgrade head

## Drop into bash shell inside api container
shell:
	docker compose exec api bash

## Full teardown — removes volumes too
clean:
	docker compose down -v --remove-orphans

## Build and start (combines build + up)
start: build up migrate

## Check health of all services
health:
	curl -s http://localhost:8000/health | python -m json.tool

## Upload the sample CSV and watch processing
upload:
	@echo "Uploading transactions.csv..."
	curl -s -X POST http://localhost:8000/jobs/upload -F "file=@transactions.csv" | python -m json.tool

## Manually trigger stale job reaper (for testing)
trigger-reaper:
	docker compose exec worker celery -A app.tasks.celery_app call reap_stale_jobs

## Check LLM provider circuit breaker status
check-llm:
	@curl -s http://localhost:8000/health | python -c "import sys,json; h=json.load(sys.stdin); print(json.dumps(h.get('llm_providers', {}), indent=2))"
