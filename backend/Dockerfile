FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
# Match the dependency lock used by CI instead of resolving newer packages.
RUN pip install --no-cache-dir uv==0.12.5 \
    && uv sync --locked --no-dev --no-editable

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
