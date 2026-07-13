IMAGE := gradbot-voice
PORT   := 8282

.PHONY: help build run test lint migrate

# `make` on its own lists the targets. Each target's `##` comment is its blurb.
.DEFAULT_GOAL := help
help:
	@echo "Gradbot voice — everything runs in Docker (gradbot has no macOS x86_64 wheel)."
	@echo
	@# The ## blurbs are read as plain text by grep, so make never expands them.
	@# Substitute the vars we reference back in by hand.
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sed -e 's/{{PORT}}/$(PORT)/' \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-9s\033[0m %s\n", $$1, $$2}'
	@echo

build: ## Build the image. Run this after any code change — nothing hot-reloads.
	docker build -t $(IMAGE) .

run: ## Serve the app on http://localhost:{{PORT}}. Needs `build` first.
	docker run --rm -it -p $(PORT):8282 --env-file server/.env $(IMAGE)

test: ## Run pytest in the container. Needs `build` first.
	docker run --rm $(IMAGE) uv run pytest

lint: ## Run ruff in the container. Needs `build` first.
	docker run --rm $(IMAGE) uv run ruff check .

migrate: ## Create the schema in Supabase. Run once, before the first call.
	psql "$$SUPABASE_DB_URL" -f server/migrations/007_framework.sql
