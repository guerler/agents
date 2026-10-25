# OLIT_ROOT names the checkout under test.
OLIT_ROOT ?= $(HOME)/orbit/browser
export OLIT_ROOT

.PHONY: format lint test check

format:
	python3 -m black .
	uvx ruff check . --fix

lint:
	python3 -m black --check .
	uvx ruff check .

test:
	python3 -m pytest tests -q

check: lint test
