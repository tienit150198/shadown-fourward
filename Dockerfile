FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && groupadd --gid 10001 shadowapp \
    && useradd --uid 10001 --gid shadowapp --create-home shadowapp
COPY --chown=shadowapp:shadowapp backend/ /app/backend/
COPY --chown=shadowapp:shadowapp web/ /app/web/
# Populate new named volumes with writable directories and the bundled sample skill.
RUN mkdir -p /data/skills /data/wardrobe /data/evals \
    && cp -a /app/backend/skills/. /data/skills/ \
    && chown -R shadowapp:shadowapp /data
ENV SHADOW_SKILLS_DIR=/data/skills \
    SHADOW_WARDROBE_DIR=/data/wardrobe \
    SHADOW_EVALS_DIR=/data/evals
USER shadowapp
WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "6291456"]
