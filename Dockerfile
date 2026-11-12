FROM node:22-bookworm-slim AS camera-kit
WORKDIR /build/web-sdk
COPY web-sdk/package.json web-sdk/package-lock.json ./
RUN npm ci
COPY web-sdk/camera-kit.js ./
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install -r requirements.txt \
    && groupadd --gid 10001 shadowapp \
    && useradd --uid 10001 --gid shadowapp --create-home shadowapp
COPY --chown=shadowapp:shadowapp backend/ /app/backend/
COPY --chown=shadowapp:shadowapp web/ /app/web/
COPY --from=camera-kit --chown=shadowapp:shadowapp /build/web/vendor/snap-camera-kit.js /app/web/vendor/snap-camera-kit.js
# Populate new named volumes with writable directories and the bundled sample skill.
RUN mkdir -p /data/skills /data/wardrobe /data/evals /data/skills-video-jobs \
    && cp -a /app/backend/skills/. /data/skills/ \
    && chown -R shadowapp:shadowapp /data
ENV SHADOW_SKILLS_DIR=/data/skills \
    SHADOW_WARDROBE_DIR=/data/wardrobe \
    SHADOW_EVALS_DIR=/data/evals
USER shadowapp
WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "6291456"]
