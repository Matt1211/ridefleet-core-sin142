FROM python:3.12-slim AS base

WORKDIR /src

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /src/app/

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

# ---------------------------------------------------------------------------
# test stage — instala deps de teste e roda pytest (SQLite in-memory, sem DB)
# ---------------------------------------------------------------------------
FROM base AS test

COPY app/requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt

COPY pytest.ini /src/pytest.ini

CMD ["pytest", "-v"]
