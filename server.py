import asyncio
import logging
import os
import tempfile
import time
import uuid

import httpx
from dotenv import load_dotenv
from elevenlabs import ElevenLabs
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("voice")

app = FastAPI(title="Audio Handler Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

eleven = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
http = httpx.AsyncClient(timeout=45)

OPENCLAW_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
OPENCLAW_TOKEN = os.environ["OPENCLAW_HOOKS_TOKEN"]
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")


@app.get("/")
async def health():
    return {"status": "ok"}


def _transcribe(audio_bytes: bytes, filename: str) -> str:
    suffix = os.path.splitext(filename)[1] or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        result = eleven.speech_to_text.convert(
            file=open(tmp_path, "rb"),
            model_id="scribe_v2",
        )
        text = result.text.strip()
        if not text:
            raise ValueError("Empty transcript")
        return text
    finally:
        os.unlink(tmp_path)


async def transcribe(audio_bytes: bytes, filename: str) -> str:
    return await asyncio.to_thread(_transcribe, audio_bytes, filename)


async def call_openclaw(transcript: str, device: str, memo_id: str) -> str:
    resp = await http.post(
        f"{OPENCLAW_URL}/hooks/voice",
        headers={"Authorization": f"Bearer {OPENCLAW_TOKEN}"},
        json={
            "message": transcript,
            "name": "Voice Memo",
            "device": device,
            "memo_id": memo_id,
            "wakeMode": "now",
            "deliver": True,
            "channel": "whatsapp",
            "timeoutSeconds": 30,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    for key in ("response", "reply", "text", "message", "content"):
        if key in data and data[key]:
            return str(data[key])
    return str(data)


def _synthesize(text: str) -> bytes:
    audio_iter = eleven.text_to_speech.convert(
        text=text,
        voice_id=VOICE_ID,
        model_id="eleven_flash_v2_5",
        output_format="mp3_44100_128",
    )
    return b"".join(audio_iter)


async def synthesize(text: str) -> bytes:
    return await asyncio.to_thread(_synthesize, text)


async def stream_openclaw(transcript: str, device: str, memo_id: str):
    async with http.stream(
        "POST",
        f"{OPENCLAW_URL}/hooks/agent",
        headers={"Authorization": f"Bearer {OPENCLAW_TOKEN}"},
        json={
            "message": transcript,
            "name": "Voice Memo",
            "device": device,
            "memo_id": memo_id,
            "wakeMode": "now",
            "deliver": True,
            "channel": "whatsapp",
            "timeoutSeconds": 30,
        },
    ) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_text():
            yield chunk


class TextInput(BaseModel):
    text: str
    device: str = "web"
    tts: bool = False


class StreamInput(BaseModel):
    text: str
    device: str = "web"


@app.post("/stream-text")
async def stream_pipeline(body: StreamInput):
    request_id = uuid.uuid4().hex[:8]
    log.info(f"[{request_id}] Streaming text from {body.device}: {body.text[:80]}...")
    memo_id = f"{request_id}-stream"

    async def generate():
        try:
            async for chunk in stream_openclaw(body.text, body.device, memo_id):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            log.error(f"[{request_id}] Stream error: {e}")
            yield f"data: {{'error': '{str(e)}'}}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/text")
async def text_pipeline(body: TextInput):
    request_id = uuid.uuid4().hex[:8]
    log.info(f"[{request_id}] Incoming text from {body.device}: {body.text[:80]}...")

    memo_id = f"{request_id}-text"
    t0 = time.monotonic()

    try:
        reply = await call_openclaw(body.text, body.device, memo_id)
    except httpx.HTTPStatusError as e:
        log.error(f"[{request_id}] OpenClaw returned {e.response.status_code}")
        raise HTTPException(status_code=502, detail="AI backend error")
    except Exception as e:
        log.error(f"[{request_id}] OpenClaw unreachable: {e}")
        raise HTTPException(status_code=502, detail="AI backend unreachable")
    t1 = time.monotonic()
    log.info(f"[{request_id}] OpenClaw done in {t1 - t0:.1f}s: {reply[:80]}...")

    if body.tts:
        try:
            audio_response = await synthesize(reply)
        except Exception as e:
            log.error(f"[{request_id}] TTS failed: {e}")
            raise HTTPException(status_code=502, detail="Speech synthesis unavailable")
        t2 = time.monotonic()
        log.info(f"[{request_id}] TTS done in {t2 - t1:.1f}s, total: {t2 - t0:.1f}s")
        return Response(
            content=audio_response,
            media_type="audio/mpeg",
            headers={"X-Request-Id": request_id, "X-Reply": reply[:200]},
        )

    log.info(f"[{request_id}] Total: {t1 - t0:.1f}s")
    return {"request_id": request_id, "reply": reply}


@app.post("/voice")
async def voice_pipeline(
    audio: UploadFile = File(...),
    device: str = Form("watch"),
):
    request_id = uuid.uuid4().hex[:8]
    log.info(f"[{request_id}] Incoming voice memo from {device}: {audio.filename}")

    audio_bytes = await audio.read()
    memo_id = f"{request_id}-{audio.filename or 'memo'}"

    # Step 1: Transcribe
    t0 = time.monotonic()
    try:
        transcript = await transcribe(audio_bytes, audio.filename or "memo.m4a")
    except Exception as e:
        log.error(f"[{request_id}] STT failed: {e}")
        raise HTTPException(status_code=502, detail="Transcription service unavailable")
    t1 = time.monotonic()
    log.info(f"[{request_id}] STT done in {t1 - t0:.1f}s: {transcript[:80]}...")

    # Step 2: Send to OpenClaw
    try:
        reply = await call_openclaw(transcript, device, memo_id)
    except httpx.HTTPStatusError as e:
        log.error(f"[{request_id}] OpenClaw returned {e.response.status_code}")
        raise HTTPException(status_code=502, detail="AI backend error")
    except Exception as e:
        log.error(f"[{request_id}] OpenClaw unreachable: {e}")
        raise HTTPException(status_code=502, detail="AI backend unreachable")
    t2 = time.monotonic()
    log.info(f"[{request_id}] OpenClaw done in {t2 - t1:.1f}s: {reply[:80]}...")

    # Step 3: Synthesize spoken response
    try:
        audio_response = await synthesize(reply)
    except Exception as e:
        log.error(f"[{request_id}] TTS failed: {e}")
        raise HTTPException(status_code=502, detail="Speech synthesis unavailable")
    t3 = time.monotonic()
    log.info(f"[{request_id}] TTS done in {t3 - t2:.1f}s, total: {t3 - t0:.1f}s")

    return Response(
        content=audio_response,
        media_type="audio/mpeg",
        headers={
            "X-Request-Id": request_id,
            "X-Transcript": transcript[:200],
            "X-Reply": reply[:200],
        },
    )
