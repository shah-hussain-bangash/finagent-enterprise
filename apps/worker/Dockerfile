# Multi-stage, non-root worker image (prompt Â§34). Same package as the API; different entrypoint.
# Pin the base by digest in CI-built release images.
FROM python:3.12-slim AS build
WORKDIR /build
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install -c requirements.lock ".[azure]"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libseccomp2 \
      poppler-utils \
      tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin finagent \
 && mkdir -p /data/blobs && chown 10001:10001 /data/blobs
COPY --from=build /install /usr/local
WORKDIR /app
USER 10001
# SIGTERM drains the loop; unfinished runs resume on the next worker from their checkpoints.
STOPSIGNAL SIGTERM
CMD ["python", "-m", "finagent.worker"]

