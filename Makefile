.PHONY: install dev test evals evals-live seed-demo up down lint

PY := .venv/bin/python

install:            ## create venv and install backend + frontend deps
	python3 -m venv .venv
	$(PY) -m pip install --quiet -e "backend[dev]"
	cd frontend && npm install

dev:                ## run API (8000) and UI (5173) locally with hot reload
	cd backend && ../$(PY) -m uvicorn loupe.main:app --reload --port 8000 & \
	cd frontend && npm run dev

seed-demo:          ## load the synthetic demo repository into the local DB
	cd backend && ../$(PY) -m loupe.cli seed-demo

test:               ## backend unit + API tests
	cd backend && ../$(PY) -m pytest -q

evals:              ## prompt eval harness with the deterministic mock provider
	cd backend && ../$(PY) -m evals.run

evals-live:         ## eval harness against the configured real provider (costs tokens)
	cd backend && ../$(PY) -m evals.run --provider $${LOUPE_LLM_PROVIDER:-anthropic}

up:                 ## docker compose: API on 8000, UI on 8080
	docker compose up --build

down:
	docker compose down
