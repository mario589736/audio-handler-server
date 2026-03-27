# Audio Handler Server

Voice pipeline for OpenClaw: Apple Watch audio in, spoken confirmation out.

```
Watch/iPhone → POST /voice (audio) → ElevenLabs STT → OpenClaw → ElevenLabs TTS → audio response
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in your keys
```

## Run

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

## Test

```bash
curl http://localhost:8000/                              # health check
./test.sh ~/path/to/voice-memo.m4a                       # full pipeline
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | / | Health check |
| POST | /voice | Audio in, audio out. Accepts multipart form: `audio` (file) + `device` (string, default "watch") |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| ELEVENLABS_API_KEY | yes | ElevenLabs API key |
| OPENCLAW_HOOKS_TOKEN | yes | OpenClaw webhook auth token |
| ELEVENLABS_VOICE_ID | no | TTS voice (default: JBFqnCBsd6RMkjVDRZzb) |
| OPENCLAW_GATEWAY_URL | no | Gateway URL (default: http://127.0.0.1:18789) |
