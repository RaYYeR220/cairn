# Cairn console — backend API + static frontend in one small image.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY console ./console

RUN pip install --no-cache-dir -e ".[console]"

# The hosted demo uses the lightweight deterministic embedder (no model download, low memory);
# the schema width matches it. Local runs use the semantic model via the [embed] extra.
ENV CAIRN_EMBEDDER=deterministic \
    CAIRN_EMBEDDING_DIM=384 \
    PORT=8080

EXPOSE 8080
CMD ["cairn-console"]
