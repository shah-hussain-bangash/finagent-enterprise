# SYNTHETIC mock bank (local/test only). Never deployed to staging or production.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MOCKBANK_ENABLE_CONTROL=1
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn>=0.30" && useradd --uid 10002 --create-home mockbank
WORKDIR /app
COPY apps/mock-bank/mockbank ./mockbank
USER 10002
EXPOSE 8100
CMD ["python", "-m", "uvicorn", "mockbank.app:app", "--host", "0.0.0.0", "--port", "8100", "--no-server-header"]
