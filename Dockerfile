# Production container for the AD Identity L1 Virtual Agent + AD Ops agents.
# Built by Samarth Pandey (hack-grp-22). Run behind Azure Container Apps / App Service with Entra ID
# authentication (AUTH_MODE=easyauth). Secrets come from the platform (Key Vault references), never the image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    AUTH_MODE=easyauth \
    RUNTIME_DIR=/app/runtime

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sample_data ./sample_data

RUN useradd --system --uid 10001 --home /app appuser && mkdir -p /app/runtime && chown -R appuser /app/runtime
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "src/serve.py"]
