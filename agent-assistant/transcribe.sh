#!/usr/bin/env bash
# Транскрибация видео локально через whisper.cpp
# Использование: ./transcribe.sh путь/к/видео.mp4

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Использование: $0 <путь к видео>"
    echo "Пример:       $0 openclaw-web-1.mp4"
    exit 1
fi

VIDEO="$1"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MODEL="$SCRIPT_DIR/models/ggml-medium.bin"
TRANSCRIPTS_DIR="$SCRIPT_DIR/transcripts"

if [ ! -f "$VIDEO" ]; then
    echo "❌ Файл не найден: $VIDEO"
    exit 1
fi

if [ ! -f "$MODEL" ]; then
    echo "❌ Модель не найдена: $MODEL"
    echo "   Скачай ggml-medium.bin в папку models/"
    exit 1
fi

mkdir -p "$TRANSCRIPTS_DIR"

BASENAME=$(basename "$VIDEO")
NAME="${BASENAME%.*}"
DATE=$(date +%Y-%m-%d)
OUTPUT_PREFIX="$TRANSCRIPTS_DIR/${DATE}_${NAME}_transcript"

TMP_WAV=$(mktemp -t whisper-audio).wav
trap "rm -f '$TMP_WAV'" EXIT

echo "🎬 Извлекаю аудио из видео..."
ffmpeg -i "$VIDEO" -ar 16000 -ac 1 -c:a pcm_s16le "$TMP_WAV" -y -loglevel error

echo "🗣  Транскрибирую (≈20–40 минут для эфира 1.5 ч)..."
whisper-cli \
    -m "$MODEL" \
    -f "$TMP_WAV" \
    -l ru \
    -otxt \
    -osrt \
    -ojf \
    -of "$OUTPUT_PREFIX"

echo ""
echo "✅ Готово. Результаты в transcripts/:"
echo "   ${OUTPUT_PREFIX##*/}.txt   — читаемый текст"
echo "   ${OUTPUT_PREFIX##*/}.srt   — субтитры с таймкодами (мин:сек)"
echo "   ${OUTPUT_PREFIX##*/}.json  — полные данные с таймингами по словам"
