FROM ghcr.io/astral-sh/uv:0.9 AS uv
FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 10001 metric && mkdir -p /data/objects /data/exports && chown -R metric:metric /app /data
USER metric
EXPOSE 8000
CMD ["metric-pulse-api"]
