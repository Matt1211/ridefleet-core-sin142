FROM python:3.12-slim

WORKDIR /app

# Instala dependências
# (build context = repo root, dockerfile = core/Dockerfile)
COPY core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do core e os módulos de conformance
COPY core/ /app/core/
COPY conformance/ /app/conformance/

WORKDIR /app/core

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
