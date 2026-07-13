SHELL := /bin/bash

IMAGE     ?= gradbot-voice
NAME      ?= gradbot-voice
PORT      ?= 8282
ENV_FILE  ?= server/.env
LOG_DIR   ?= logs

.DEFAULT_GOAL := help
.PHONY: help build run stop restart log log-save sh test-health test lint migrate clean

# `make` on its own lists the targets. Each target's `##` comment is its blurb.
help:
	@echo "Gradbot voice — everything runs in Docker (gradbot has no macOS x86_64 wheel)."
	@echo
	@# The ## blurbs are read as plain text by grep, so make never expands them.
	@# Substitute the vars we reference back in by hand.
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sed -e 's/{{PORT}}/$(PORT)/' \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'
	@echo

# ---- Docker ---------------------------------------------------------

build: ## Build the image. Needed after any change to server/*.py or pyproject.
	docker build -t $(IMAGE) .

run: ## Start the app detached on http://localhost:{{PORT}}.
	@# personas/ and server/static/ are mounted read-only, so persona tweaks and
	@# frontend edits take effect on `make restart` (or a browser reload) with no
	@# rebuild. Python changes still need `make build`.
	docker run --rm -d \
		--name $(NAME) \
		--env-file $(ENV_FILE) \
		-v "$(CURDIR)/personas:/app/personas:ro" \
		-v "$(CURDIR)/server/static:/app/server/static:ro" \
		-p $(PORT):8282 \
		$(IMAGE)
	@echo "running at http://localhost:$(PORT)"

stop: ## Stop the running container.
	-@docker stop $(NAME)

restart: stop run ## Stop the container, then start it again.

log: ## Follow the running app's logs.
	@if docker ps -q -f name='^$(NAME)$$' | grep -q .; then \
		docker logs -f $(NAME) 2>&1; \
	else \
		echo "Nothing running — start the app with \`make run\` first."; \
	fi

log-save: ## Follow the logs to screen AND logs/local-<timestamp>.log.
	@mkdir -p $(LOG_DIR)
	@ts=$$(date +%Y-%m-%dT%H%M%S); f="$(LOG_DIR)/local-$$ts.log"; \
	echo "capturing to $$f (Ctrl-C to stop)"; \
	set -o pipefail; docker logs -f $(NAME) 2>&1 | tee "$$f"

sh: ## Open a shell inside the running container.
	docker exec -it $(NAME) /bin/bash

test-health: ## Hit /health on the running container; waits up to 30s for startup.
	@for i in $$(seq 1 30); do \
		if curl -fsS http://localhost:$(PORT)/health 2>/dev/null; then \
			echo ""; exit 0; \
		fi; sleep 1; \
	done; echo "health check FAILED after 30s"; exit 1

# ---- Checks ---------------------------------------------------------

test: ## Run pytest in the container. Needs `build` first.
	docker run --rm $(IMAGE) uv run pytest

lint: ## Run ruff in the container. Needs `build` first.
	docker run --rm $(IMAGE) uv run ruff check .

# ---- Database -------------------------------------------------------

migrate: ## Create the schema in Supabase. Run once, before the first call.
	psql "$$SUPABASE_DB_URL" -f server/migrations/001_schema.sql

clean: ## Remove the locally built image.
	-docker rmi $(IMAGE)
