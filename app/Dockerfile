FROM python:3.12-slim

WORKDIR /src

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /src/app/

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
