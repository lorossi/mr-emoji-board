FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY settings.env ./
COPY mr_board ./mr_board
RUN uv sync --locked --no-dev

RUN useradd --create-home board && mkdir /app/state && chown board /app/state
USER board

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "mr_board.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
