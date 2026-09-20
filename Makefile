# Common commands. Run from the repo root.
PY=python3
export PYTHONPATH=src

setup:            ## create venv and install dependencies
	$(PY) -m venv .venv && . .venv/bin/activate && pip install -U pip && pip install -r requirements.txt

ingest:           ## index every downloaded policy document
	$(PY) -m trailheadrx ingest

status:           ## what is indexed, which mode
	$(PY) -m trailheadrx status

eval:             ## run the golden scenarios and print the scorecard
	$(PY) evals/run.py

test:             ## unit tests for rules-as-code and guardrails (no model calls)
	$(PY) -m pytest -q tests

refresh:          ## re-fetch every source, re-index what changed (no email unless configured)
	$(PY) -m trailheadrx refresh

sweep:            ## precompute the menu, report drift (costs money; --dry to count)
	$(PY) -m trailheadrx sweep --dry

deploy:           ## ship to Fly.io (see docs/DEPLOY.md)
	fly deploy

web:              ## run the website locally at http://127.0.0.1:8000
	$(PY) -m uvicorn trailheadrx.web.app:app --reload --port 8000

.PHONY: setup ingest status eval test web deploy refresh sweep
