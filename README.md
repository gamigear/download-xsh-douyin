# Standalone download bots for Mac Docker

This bundle runs Telegram ingress bots on Docker Desktop for Mac and routes requests to two downloader lanes:

- Douyin bot
- Xiaohongshu bot

It does not depend on the OpenClaw runtime, OpenClaw services, or OpenClaw paths at runtime.

## Architecture

- `xhs-runtime`
  - vendor `XHS-Downloader`
  - exposed on `http://localhost:5556`
- `gamigear-bot`
  - standalone Telegram long-poller
  - routes Douyin links to the Douyin browser lane
  - routes Xiaohongshu links to the Xiaohongshu downloader lane
- `quangia-bot`
  - standalone Telegram long-poller
  - routes Douyin links to the Douyin browser lane
  - routes Xiaohongshu links to the Xiaohongshu downloader lane

## Files

- `docker-compose.yml`
- `Dockerfile`
- `app/`
- `env/gamigear.env.example`
- `env/quangia.env.example`
- `scripts/up.sh`
- `scripts/logs.sh`
- `scripts/health.sh`

## Setup

1. Copy one or more bot env examples and put the real token in them:

```bash
cp env/gamigear.env.example env/gamigear.env
cp env/quangia.env.example env/quangia.env
```

2. Start the stack:

```bash
cd .openclaw-workspace/download-bots-standalone
bash scripts/up.sh
```

3. Check status:

```bash
bash scripts/health.sh
```

4. View logs:

```bash
bash scripts/logs.sh gamigear-bot
```

## Runtime data

The stack writes downloads and poller state under:

- `data/douyin-fetch-jobs/`
- `data/douyin-runtime/`
- `data/xhs-fetch-jobs/`
- `data/xhs-runtime/`
- `data/gamigear-runtime/`
- `data/quangia-runtime/`

## Notes

- Each Telegram bot token is used by one active poller only.
- Douyin now runs primarily via headless Chromium netlog plus DOM capture inside each bot container.
- Playwright remains as a fallback probe for Douyin DOM/media edge cases.
- `xhs-runtime` is the Xiaohongshu downloader backend and remains unchanged.
- No `workspace-download`, `media-ingest-service`, or OpenClaw process is required for the standalone stack.
