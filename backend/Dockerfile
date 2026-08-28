FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY config /app/config
COPY db /app/db
COPY scripts /app/scripts

ENV PYTHONPATH=/app/backend:/app \
    LF_CONFIG_DIR=/app/config \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["sh", "-c", "python -m scripts.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
