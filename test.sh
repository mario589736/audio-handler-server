#!/bin/bash
set -e

SERVER="http://localhost:8000"

echo "=== Health check ==="
curl -s "$SERVER/" | python3 -m json.tool

echo ""
echo "=== Voice pipeline ==="
if [ -z "$1" ]; then
    echo "Usage: ./test.sh <path-to-audio-file>"
    echo "Example: ./test.sh ~/voice-memo.m4a"
    exit 1
fi

curl -X POST "$SERVER/voice" \
    -F "audio=@$1" \
    -F "device=watch" \
    -v \
    --output response.mp3

echo ""
echo "=== Playing response ==="
afplay response.mp3
