FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/data/whisper-cache

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

# faster-whisper (ASR tiếng Trung cho vietsub). CPU int8; model tải lần đầu vào HF_HOME (mount để cache).
# edge-tts + pydub: lồng tiếng (voiceover) tiếng Việt.
RUN pip install --no-cache-dir faster-whisper edge-tts pydub

COPY package.json /app/package.json
RUN npm install --omit=dev

COPY app /app/app

RUN chmod +x /app/app/*.py

CMD ["python", "/app/app/lane_poller.py", "douyin"]
