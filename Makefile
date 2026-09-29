# OLIT_ROOT names the checkout under test; the eval imports its brain from source.
OLIT_ROOT ?= $(HOME)/galaxy-visualizations/packages/olit
export OLIT_ROOT

.PHONY: format lint test check upstream

format:
	python3 -m black .
	uvx ruff check . --fix

lint:
	python3 -m black --check .
	uvx ruff check .

test:
	python3 -m pytest tests -q

check: lint test

# Read-only: fetches refs, reports drift, adopts nothing.
upstream:
	python3 upstream_check.py
