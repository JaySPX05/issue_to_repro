# Issue-to-Repro: API + web UI in one container.
# Code runs in Nebius Token Factory Sandboxes (SANDBOX_BACKEND=nebius), models on Token Factory.
#
#   docker build -t issue-to-repro .
#   docker run -p 7860:7860 --env-file .env issue-to-repro      -> http://localhost:7860
#
# Secrets (NEBIUS_API_KEY, NEBIUS_PROJECT_ID, MODEL_*, GITHUB_TOKEN) come from the host's
# environment / secret settings at run time. They are never copied into the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=7860 \
    SANDBOX_BACKEND=nebius \
    ITR_DB_PATH=/data/itr.db \
    WEB_DIR=/app/web \
    SEED_DIR=/app/demo_runs \
    MAX_CONCURRENT_RUNS=2

WORKDIR /app

# Dependencies first so code edits do not reinstall them.
COPY pyproject.toml LICENSE ./
COPY src ./src
RUN pip install ".[web,nebius]"

COPY web ./web
COPY demo_runs ./demo_runs

# Run as an unprivileged user with a writable data folder (uid 1000 also suits Hugging Face Spaces).
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/health', timeout=4)"

CMD ["python", "-m", "itr.api"]
