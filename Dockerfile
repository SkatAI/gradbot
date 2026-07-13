# Python 3.12 — gradbot publishes no cp311 wheel.
#
# This image is also how the app runs locally: gradbot ships macOS wheels for
# arm64 only, so on an Intel Mac there is no importable build outside a
# linux/amd64 container (the manylinux_2_17_x86_64 wheel installs here fine).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY server/pyproject.toml server/uv.lock* /app/server/
WORKDIR /app/server
# Dev deps are installed too: the test suite has to run in here as well — see
# the header comment. `uv run --no-dev` at CMD keeps them out of the run env.
RUN uv sync --no-install-project

WORKDIR /app
COPY server /app/server
COPY personas /app/personas

WORKDIR /app/server
ENV PYTHONUNBUFFERED=1
EXPOSE 8282
CMD ["uv", "run", "--no-dev", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8282"]
