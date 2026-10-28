FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra server --no-install-project

COPY openscour ./openscour
RUN uv sync --frozen --no-dev --extra server

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "openscour-server", "--host", "0.0.0.0", "--port", "8000"]
