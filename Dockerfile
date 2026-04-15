FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    curl \
    ffmpeg \
    nodejs \
    npm \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json /app/package.json
RUN npm install --omit=dev

COPY app /app/app

RUN chmod +x /app/app/*.py

CMD ["python", "/app/app/lane_poller.py", "douyin"]
