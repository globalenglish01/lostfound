FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 构建期预下载多语言句向量模型，运行时不需要联网
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV LF_MODEL_CACHE=/models
RUN python -c "from fastembed import TextEmbedding;     TextEmbedding(model_name='${EMBEDDING_MODEL}', cache_dir='/models')"

COPY backend /app/backend
COPY config /app/config
COPY db /app/db
COPY scripts /app/scripts

ENV PYTHONPATH=/app/backend:/app \
    LF_CONFIG_DIR=/app/config \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["sh", "-c", "python -m scripts.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
