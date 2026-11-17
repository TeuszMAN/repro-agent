.PHONY: verify lint typecheck test

# Single verification command: nothing is done until this passes.
verify: lint typecheck test

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest
