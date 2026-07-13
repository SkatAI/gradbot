IMAGE := gradbot-voice
NAME  := gradbot-voice
PORT   := 8282

.PHONY: help build run log test lint migrate

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
	docker run --rm -it --name $(NAME) -p $(PORT):8282 --env-file server/.env $(IMAGE)

log: ## Follow the running app's logs. Use from a second terminal while `run` is up.
	@# 2>&1 because uvicorn and loguru both write to stderr; without it you would
	@# see the request log and none of the app's own output.
	@if docker ps -q -f name='^$(NAME)$$' | grep -q .; then \
		docker logs -f $(NAME) 2>&1; \
	else \
		echo "Nothing running — start the app with \`make run\` first."; \
	fi

test: ## Run pytest in the container. Needs `build` first.
	docker run --rm $(IMAGE) uv run pytest

lint: ## Run ruff in the container. Needs `build` first.
	docker run --rm $(IMAGE) uv run ruff check .

migrate: ## Create the schema in Supabase. Run once, before the first call.
	psql "$$SUPABASE_DB_URL" -f server/migrations/007_framework.sql
