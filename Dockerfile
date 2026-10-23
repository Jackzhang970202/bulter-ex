FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KNOWLENS_HOME=/data \
    KNOWLENS_HOST=0.0.0.0 \
    KNOWLENS_PORT=8633

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY knowlens ./knowlens
COPY web ./web
COPY vendor ./vendor
COPY scripts ./scripts
COPY run.py ./

VOLUME ["/data"]
EXPOSE 8633

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8633/api/status',timeout=4)" || exit 1

CMD ["python", "run.py"]