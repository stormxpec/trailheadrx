# Trailhead Rx on Fly.io (session 5).
#
# What goes in the image: the code, the corpus documents, the built index,
# and the embedding model — everything an answer needs, so a machine starts
# ready and never fetches at request time. The corpus documents are payer-
# published files; they are kept out of the PUBLIC REPO (.gitignore) but
# belong in the PRIVATE IMAGE, which is built from Ben's working copy.
#
# What stays out: secrets (set with `fly secrets`), and anything written at
# run time (audit trace, plan-name requests) — those go to the /data volume.
FROM python:3.11-slim
WORKDIR /app

# CPU-only PyTorch first: the default wheel pulls CUDA and triples the image.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONPATH=/app/src \
    HF_HOME=/app/.hf \
    TRAILHEADRX_AUDIT_DIR=/data/audit \
    TRAILHEADRX_REQUESTED_PLANS=/data/requested_plans.jsonl \
    TRAILHEADRX_REFRESH_STATE=/data/refresh_state.json \
    TRAILHEADRX_CACHE_DIR=/data/cache \
    TRAILHEADRX_SWEEP_RECORD=/data/sweep_record.json \
    TRAILHEADRX_HTTPS=1 \
    PORT=8080

# Build the index and cache the embedding model into the image (no API key
# needed: ingestion is local). A deploy therefore re-indexes whatever is in
# corpus/ at that moment — the daily refresh (backlog) will update in place.
RUN python -m trailheadrx ingest --rebuild && python -m trailheadrx status

EXPOSE 8080
CMD ["sh", "-c", "uvicorn trailheadrx.web.app:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
