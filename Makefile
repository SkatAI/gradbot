IMAGE := gradbot-voice
PORT   := 8080

.PHONY: build run test lint migrate

# Local runs go through Docker: gradbot has no macOS x86_64 wheel, so on an
# Intel Mac the linux/amd64 container is the only place it imports.
build:
	docker build -t $(IMAGE) .

run:
	docker run --rm -it -p $(PORT):8080 --env-file server/.env $(IMAGE)

# Tests run in the container for the same reason `run` does — gradbot does not
# import on an Intel Mac. Depends on `build`.
test:
	docker run --rm $(IMAGE) uv run pytest

lint:
	docker run --rm $(IMAGE) uv run ruff check .

# Apply the one migration this app adds to the shared Supabase DB.
migrate:
	psql "$$SUPABASE_DB_URL" -f server/migrations/007_framework.sql
