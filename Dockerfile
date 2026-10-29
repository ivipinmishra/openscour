FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra server --no-install-project

COPY openscour ./openscour
RUN uv sync --frozen --no-dev --extra server

# Run as an unprivileged user; the app only reads its own files.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin openscour
USER openscour

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# Run the installed command directly: `uv run` would try to write to a cache.
CMD ["/app/.venv/bin/openscour-server", "--host", "0.0.0.0", "--port", "8000"]
