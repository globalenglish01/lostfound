FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 构建期预下载模型，运行时不需要联网、不需要任何 token。
# 迭代时若 /models 卷里已经有模型，可以跳过这一步省几分钟：
#   docker compose build api --build-arg PREFETCH_MODELS=0
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-mpnet-base-v2
ARG IMAGE_MODEL=Qdrant/clip-ViT-B-32-vision
ARG IMAGE_TEXT_MODEL=Qdrant/clip-ViT-B-32-text
ARG PREFETCH_MODELS=1
ENV LF_MODEL_CACHE=/models
RUN if [ "$PREFETCH_MODELS" = "1" ]; then       python -c "from fastembed import TextEmbedding, ImageEmbedding;         TextEmbedding(model_name='${EMBEDDING_MODEL}', cache_dir='/models');         ImageEmbedding(model_name='${IMAGE_MODEL}', cache_dir='/models');         TextEmbedding(model_name='${IMAGE_TEXT_MODEL}', cache_dir='/models')";     else echo "PREFETCH_MODELS=0 -> 跳过模型预下载（依赖 /models 卷）"; fi

COPY backend /app/backend
COPY config /app/config
COPY db /app/db
COPY scripts /app/scripts
COPY domain /app/domain

ENV PYTHONPATH=/app/backend:/app \
    LF_CONFIG_DIR=/app/config \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["sh", "-c", "python -m scripts.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
