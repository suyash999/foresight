# External Emerging Collectibles Intelligence Agent
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# System deps for lxml / builds
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libxml2-dev libxslt1-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# sgmllib3k (feedparser dep) ships a broken sdist on modern setuptools; install
# feedparser without it, then vendor the single pure-python module.
RUN pip install --upgrade pip && \
    pip install -r requirements.txt || true && \
    pip install --no-deps feedparser && \
    python -c "import sgmllib" 2>/dev/null || \
    (pip download --no-deps --no-binary :all: sgmllib3k -d /tmp/sg && \
     cd /tmp/sg && tar xzf sgmllib3k-*.tar.gz && \
     cp sgmllib3k-*/sgmllib.py "$(python -c 'import site;print(site.getsitepackages()[0])')/")

COPY . .

RUN mkdir -p data outputs

EXPOSE 8501

# Default: run the continuous worker. docker-compose overrides for the dashboard.
CMD ["python", "-m", "emerging_collectibles_agent.main", "run-forever", "--config", "config.yaml"]
