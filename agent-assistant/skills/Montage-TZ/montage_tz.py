#!/usr/bin/env python3
"""
montage_tz.py — драйвер сборки черновика ТЗ на монтаж эфира.

Идея (см. docs/2026-06-04_montage-tz-skill_doc.md):
  «дай источник записи + окна шеринга экрана → получи черновик ТЗ за минуты».

Два слоя анализа:
  Слой 1 — транскрипт по ВСЕЙ записи (whisper): даты/цены/заминки в РЕЧИ.
  Слой 2 — плотный OCR только по ОКНАМ шеринга экрана: даты/секреты на ЭКРАНЕ.

Что НЕ делает: не принимает решений «резать/блюрить/оставить» (это video-edit-assistant
и автор) и НЕ отправляет ничего во внешние каналы. На выходе — ЧЕРНОВИК на проверку.

Безопасные дефолты под слабые машины: whisper base, 480p, OCR-шаг 5 сек, кадр-шаг 30 сек.

Использование:
  python3 montage_tz.py --source <kinescope_id|путь_к_видео> \\
      --windows "20m-50m,1h15m-1h40m" \\
      --theme webinar-openclaw

Полный список опций: python3 montage_tz.py --help
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Константы / дефолты
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
# Корень проекта agent-assistant (skills/Montage-TZ/ -> ../../)
AGENT_ROOT = SCRIPT_DIR.parent.parent
MODELS_DIR = AGENT_ROOT / "models"
DEFAULT_WORK_DIR = AGENT_ROOT / "source" / ".montage-cache"
DEFAULT_OUT_DIR = AGENT_ROOT / "reports"

WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model}.bin"
KINESCOPE_API = "https://api.kinescope.io/v1/videos/{id}"

REQUIRED_TOOLS = ["ffmpeg", "ffprobe", "whisper-cli", "tesseract"]

# ─────────────────────────────────────────────────────────────────────────────
# Паттерны находок
# ─────────────────────────────────────────────────────────────────────────────

# Даты (речь и экран): числовые, рус-месяцы, англ-месяцы/дни (терминал), годы.
RE_DATE_NUMERIC = re.compile(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b")
RE_DATE_RU_MONTH = re.compile(
    r"\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*",
    re.IGNORECASE,
)
RE_DATE_EN = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
    r"\s+\d{1,2}\b",
    re.IGNORECASE,
)
RE_YEAR = re.compile(r"\b20(?:2[3-9]|3\d)\b")  # 2023-2039, чтобы отсечь случайные числа
RE_REL_TIME = re.compile(
    r"\b(?:сегодня|вчера|завтра|на\s+(?:этой|следующей|прошлой)\s+неделе|"
    r"в\s+(?:этом|прошлом|следующем)\s+(?:году|месяце)|"
    r"в\s+\d{2}\s+году|этим\s+летом|этой\s+(?:зимой|весной|осенью))\b",
    re.IGNORECASE,
)

# Цены / офферы в речи.
# RE_OFFER — только сильные сигналы оффера/дедлайна (то, что реально устаревает и режется).
# Слабые «бесплатн»/«тариф» убраны после обкатки 04.06 — давали много шумных срабатываний.
RE_PRICE = re.compile(r"\b\d[\d\s]{2,}\s*(?:₽|руб|р\.|рубл\w*|тыс\w*|\$|долл\w*)", re.IGNORECASE)
RE_OFFER = re.compile(
    r"\b(?:скидк\w*|промокод\w*|купон\w*|рассрочк\w*|акци\w*|спецпредложен\w*|предзаказ\w*)\b",
    re.IGNORECASE,
)

# Тех. заминки в речи
RE_GLITCH = re.compile(
    r"(?:не\s+открыва|перезагруз|переподключ|вторая\s+попытк|давайте\s+подожд|"
    r"завис|не\s+слышно|не\s+видно|пропал\s+звук|техническ|не\s+работает|секунд\w*\s+подожд)",
    re.IGNORECASE,
)

# Секреты на экране (OCR)
RE_SECRET = [
    ("Last login", re.compile(r"last\s+login", re.IGNORECASE)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")),
    ("API-ключ", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9][A-Za-z0-9_-]{16,}")),
    ("токен/секрет", re.compile(
        r"(?:api[_-]?key|access[_-]?token|secret|bearer|token)\s*[:=]?\s*[A-Za-z0-9][A-Za-z0-9_\-.]{15,}",
        re.IGNORECASE)),
    ("IP-адрес", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
]

# ─────────────────────────────────────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"❌ {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)


def hms(seconds: float) -> str:
    """Секунды → H:MM:SS."""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


_SUFFIX_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?\s*$", re.IGNORECASE)


def parse_tc(tc: str) -> float:
    """Таймкод → секунды. Два однозначных формата:
       суффиксный  — '20m', '1h15m30s', '90s', '50m'  (рекомендуется для окон);
       двоеточный  — 'HH:MM:SS' или 'MM:SS' (последняя часть — секунды).
    'MM:SS' и 'HH:MM:SS' трактуются по медиа-стандарту: '20:00' = 20 минут, '1:40:00' = 1ч40м."""
    tc = tc.strip()
    if ":" in tc:
        parts = [int(p) for p in tc.split(":")]
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, m, s = 0, parts[0], parts[1]
        else:
            h, m, s = 0, 0, parts[0]
        return h * 3600 + m * 60 + s
    sm = _SUFFIX_RE.match(tc)
    if sm and any(sm.groups()):
        h, m, s = (int(g) if g else 0 for g in sm.groups())
        return h * 3600 + m * 60 + s
    if tc.isdigit():  # голое число — секунды
        return float(tc)
    die(f"Не разобрал таймкод: '{tc}'. Форматы: 20m / 1h15m30s / MM:SS / HH:MM:SS")
    return 0.0  # недостижимо


def load_notes(args) -> list[str]:
    """Собрать заметки автора из --notes (через ';' или перевод строки) и --notes-file."""
    raw: list[str] = []
    if args.notes:
        raw.extend(re.split(r"[;\n]", args.notes))
    if getattr(args, "notes_file", ""):
        path = Path(args.notes_file)
        if not path.exists():
            die(f"Файл заметок не найден: {path}")
        raw.extend(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    return [s.strip() for s in raw if s.strip()]


def split_note(note: str) -> tuple[str, str]:
    """Заметку → (таймкод_для_показа, текст). Если строка начинается с таймкода
    (35:00 / 1:15:30 / 1h15m), выносим его в колонку тайминга; иначе тайминг пуст.
    Голые числа таймкодом НЕ считаем (990 — это цена, а не время)."""
    parts = note.split(maxsplit=1)
    if len(parts) == 2:
        token = parts[0].rstrip("—-|:")
        is_colon = ":" in token and re.fullmatch(r"\d{1,2}(?::\d{2}){1,2}", token)
        is_suffix = re.fullmatch(r"(?=.*\d)(?:\d+h)?(?:\d+m)?(?:\d+s)?", token)
        if is_colon or is_suffix:
            text = parts[1].lstrip("—-|: ").strip()
            return token, text or note
    return "", note


def parse_windows(spec: str) -> list[tuple[float, float]]:
    """'0:20-0:50,1:15:30-1:40' → [(20.0,50.0),(4530.0,6000.0)] (в секундах)."""
    windows = []
    for chunk in filter(None, (c.strip() for c in spec.split(","))):
        if "-" not in chunk:
            die(f"Окно без диапазона: '{chunk}'. Формат: СТАРТ-КОНЕЦ, напр. 0:20-0:50")
        a, b = chunk.split("-", 1)
        start, end = parse_tc(a), parse_tc(b)
        if end <= start:
            die(f"Окно '{chunk}': конец не позже старта.")
        windows.append((start, end))
    return windows


def preflight(args) -> None:
    """Проверка инструментов и языков OCR до начала работы."""
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        die(
            "Не найдены инструменты: " + ", ".join(missing) + ".\n"
            "   Поставь: ffmpeg, whisper-cli (whisper.cpp), tesseract.\n"
            "   macOS: brew install ffmpeg tesseract whisper-cpp"
        )
    # Языки tesseract
    try:
        out = run(["tesseract", "--list-langs"], capture_output=True, text=True).stderr or ""
        out += run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout or ""
    except Exception:
        out = ""
    if args.ocr_lang not in out:
        log(
            f"⚠️  tesseract: язык '{args.ocr_lang}' не установлен (есть: "
            f"{', '.join(re.findall(r'^[a-z]{3}$', out, re.M)) or '—'}).\n"
            f"   Даты/латиница/терминал OCR-ит и на eng. Для русского текста в интерфейсе\n"
            f"   доставь rus: скачай rus.traineddata в папку tessdata (--list-langs покажет путь)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Источник записи
# ─────────────────────────────────────────────────────────────────────────────

def resolve_source(args, work_dir: Path) -> tuple[Path, str]:
    """Вернёт (путь_к_видео, ключ_кэша). Источник — локальный файл или Kinescope id."""
    src = args.source
    if Path(src).exists():
        key = Path(src).stem
        return Path(src).resolve(), key

    # Иначе это Kinescope id или ссылка на Kinescope (https://kinescope.io/<id>)
    video_id = src
    if src.startswith("http") or "kinescope.io" in src:
        from urllib.parse import urlparse
        path = urlparse(src).path.strip("/")
        if path:
            video_id = path.split("/")[-1]
    token = os.environ.get("KINESCOPE_API_TOKEN")
    if not token:
        die(
            f"Источник '{src}' — не локальный файл, а Kinescope (id: {video_id}).\n"
            f"   Нужен токен: задай KINESCOPE_API_TOKEN в окружении\n"
            f"   (для бота — строкой в launch-pult/.env), либо передай локальный путь к видео."
        )
    log(f"🔎 Запрашиваю запись в Kinescope: {video_id}")
    req = urllib.request.Request(
        KINESCOPE_API.format(id=video_id), headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        die(f"Kinescope API не ответил: {e}")
    video = data.get("data", data)
    assets = video.get("assets", [])
    # Выбираем нужное качество, иначе минимальное доступное
    want = args.quality
    chosen = next((a for a in assets if a.get("quality") == want), None)
    if chosen is None:
        ordered = sorted(assets, key=lambda a: a.get("file_size", 0))
        chosen = ordered[0] if ordered else None
    if chosen is None or not chosen.get("url"):
        die("В ответе Kinescope нет скачиваемого asset с url.")
    log(f"   качество: {chosen.get('quality')} ({chosen.get('resolution','?')})")

    key = video_id
    out = work_dir / f"{key}_{chosen.get('quality','src')}.mp4"
    if out.exists() and not args.refresh:
        log(f"   кэш: {out.name} (пропускаю скачивание)")
        return out, key
    log("⬇️  Скачиваю запись (это может занять время)...")
    tmp = out.with_name(out.name + ".part")
    rc = run(["curl", "-fL", "--retry", "5", "--retry-delay", "2",
              "--retry-all-errors", "-C", "-", "-o", str(tmp), chosen["url"]]).returncode
    if rc != 0:
        die("Скачивание не удалось (CDN оборвал передачу). Повтори задачу — "
            "докачается с места обрыва (.part сохранён); либо передай локальный путь к видео.")
    tmp.replace(out)
    return out, key


def probe_duration(video: Path) -> float:
    out = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Слой 1: транскрипт (whisper по всей записи)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_model(model: str) -> Path:
    path = MODELS_DIR / f"ggml-{model}.bin"
    if path.exists():
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    url = WHISPER_MODEL_URL.format(model=model)
    log(f"⬇️  Скачиваю модель whisper '{model}' (один раз)...")
    rc = run(["curl", "-fL", "--retry", "3", "-o", str(path), url]).returncode
    if rc != 0:
        die(f"Не удалось скачать модель {model}.")
    return path


def transcribe(video: Path, key: str, args, work_dir: Path) -> Path:
    """Возвращает путь к .srt. Кэширует по ключу записи."""
    prefix = work_dir / f"{key}_transcript"
    srt = Path(f"{prefix}.srt")
    if srt.exists() and not args.refresh:
        log(f"📝 Транскрипт из кэша: {srt.name}")
        return srt
    model = ensure_model(args.whisper_model)
    wav = work_dir / f"{key}.wav"
    log("🎬 Извлекаю аудио (16 кГц моно)...")
    run(["ffmpeg", "-i", str(video), "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", str(wav), "-y", "-loglevel", "error"])
    log(f"🗣  Транскрибирую (модель {args.whisper_model}, язык {args.lang})...")
    run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", args.lang,
         "-otxt", "-osrt", "-ojf", "-of", str(prefix)])
    wav.unlink(missing_ok=True)
    if not srt.exists():
        die("whisper не создал .srt — проверь модель/аудио.")
    return srt


def parse_srt(srt: Path) -> list[tuple[float, float, str]]:
    """[(start_sec, end_sec, text), ...]"""
    segs = []
    blocks = re.split(r"\n\s*\n", srt.read_text(encoding="utf-8", errors="ignore"))
    tc_re = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})")
    for blk in blocks:
        m = tc_re.search(blk)
        if not m:
            continue
        h1, m1, s1, _, h2, m2, s2 = m.groups()
        start = int(h1) * 3600 + int(m1) * 60 + int(s1)
        end = int(h2) * 3600 + int(m2) * 60 + int(s2)
        lines = blk.splitlines()
        text = " ".join(l for l in lines[2:] if l.strip()) if len(lines) > 2 else ""
        if text:
            segs.append((float(start), float(end), text.strip()))
    return segs


def scan_transcript(segs) -> dict[str, list[tuple[float, str, str]]]:
    """Категории речевых находок: (timecode_sec, фраза, что нашли)."""
    found = {"dates": [], "prices": [], "glitches": []}
    for start, _end, text in segs:
        for rx, tag in [(RE_DATE_NUMERIC, "дата"), (RE_DATE_RU_MONTH, "дата"),
                        (RE_REL_TIME, "относит. время"), (RE_YEAR, "год")]:
            for mt in rx.findall(text):
                hit = mt if isinstance(mt, str) else " ".join(mt)
                found["dates"].append((start, text, hit))
        if RE_PRICE.search(text) or RE_OFFER.search(text):
            found["prices"].append((start, text, "цена/оффер"))
        if RE_GLITCH.search(text):
            found["glitches"].append((start, text, "тех. заминка"))
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Слой 2: OCR по окнам шеринга экрана
# ─────────────────────────────────────────────────────────────────────────────

def scan_windows(video: Path, key: str, windows, args, work_dir: Path) -> list[tuple[float, str, str]]:
    """[(timecode_sec, что_нашли, распознанный_фрагмент), ...] по экранным окнам."""
    hits = []
    frames_dir = work_dir / f"{key}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for wi, (start, end) in enumerate(windows):
        dur = end - start
        log(f"🖼  OCR окна {wi+1}/{len(windows)}: {hms(start)}–{hms(end)} "
            f"(каждые {args.ocr_step} сек)...")
        pattern = str(frames_dir / f"w{wi:02d}_%05d.jpg")
        run(["ffmpeg", "-ss", str(start), "-to", str(end), "-i", str(video),
             "-vf", f"fps=1/{args.ocr_step}", "-q:v", "3", pattern,
             "-y", "-loglevel", "error"])
        frames = sorted(frames_dir.glob(f"w{wi:02d}_*.jpg"))
        for idx, frame in enumerate(frames):
            tc = start + idx * args.ocr_step
            if tc > end:
                break
            txt = ocr_frame(frame, args.ocr_lang)
            if not txt:
                continue
            # даты на экране. Голый год (RE_YEAR) намеренно НЕ ищем в OCR — после обкатки
            # 04.06 он шумел (мисриды цифр в таблицах: 2028/2029/2030). Полные даты и
            # Jun 3 ловятся RE_DATE_NUMERIC/RE_DATE_EN, этого достаточно для блюра.
            for rx in (RE_DATE_NUMERIC, RE_DATE_EN):
                for mt in rx.findall(txt):
                    hit = mt if isinstance(mt, str) else " ".join(mt)
                    hits.append((tc, "дата на экране", hit))
            # секреты
            for label, rx in RE_SECRET:
                m = rx.search(txt)
                if m:
                    hits.append((tc, f"секрет: {label}", m.group(0)[:60]))
    return dedup_hits(hits)


def ocr_frame(frame: Path, lang: str) -> str:
    try:
        res = run(["tesseract", str(frame), "stdout", "-l", lang],
                  capture_output=True, text=True)
        return res.stdout
    except Exception:
        return ""


def dedup_hits(hits):
    """Схлопываем одинаковые находки в соседних кадрах в интервал."""
    hits.sort(key=lambda h: (h[1], h[2], h[0]))
    merged = []
    for tc, what, frag in hits:
        if merged and merged[-1][1] == what and merged[-1][2] == frag and tc - merged[-1][3] <= 15:
            merged[-1] = (merged[-1][0], what, frag, tc)  # продлеваем конец
        else:
            merged.append((tc, what, frag, tc))
    return [(m[0], m[1], m[2], m[3]) for m in merged]


# ─────────────────────────────────────────────────────────────────────────────
# Сборка черновика ТЗ
# ─────────────────────────────────────────────────────────────────────────────

def build_map(duration: float) -> str:
    rows = []
    t = 0.0
    while t < duration:
        rows.append(f"| {hms(t)}–{hms(min(t + 600, duration))} | — (заполнить) |")
        t += 600
    return "\n".join(rows)


def shorten(text: str, n: int = 90) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def assemble(out_path: Path, src_label, duration, windows, t_found, v_hits, args, notes=None) -> None:
    notes = notes or []
    d = dt.date.today().isoformat()
    L = []
    L.append(f"# ТЗ на монтаж — {args.theme} (ЧЕРНОВИК)\n")
    L.append("> ⚠️ **ЧЕРНОВИК, собран автоматически.** Перед отдачей монтажёру — проверка "
             "`video-edit-assistant` и сверка с автором. Решения «резать/блюрить/оставить» "
             "принимает человек, скрипт только размечает кандидатов.\n")
    L.append(f"**Источник:** {src_label}")
    L.append(f"**Длительность:** {hms(duration)}")
    L.append(f"**Окон шеринга экрана отсканировано:** {len(windows)}"
             + ("" if windows else " — ⚠️ окна не заданы, экранный слой пуст"))
    L.append(f"**Собрано:** {d} · модель whisper `{args.whisper_model}`, OCR `{args.ocr_lang}`\n")
    L.append("---\n")

    # 0. Заявлено автором — высокоуверенная затравка (известные моменты от человека)
    L.append("## Заявлено автором (высокая уверенность)\n")
    if notes:
        L.append("| Тайминг | Заявлено автором |")
        L.append("|---|---|")
        for n in notes:
            tc, text = split_note(n)
            L.append(f"| {tc or '—'} | {text} |")
        L.append("\n> Затравка от автора: проверять не нужно, дополнить — нужно. "
                 "Ниже автоматические находки скрипта.\n")
    else:
        L.append("_Автор не указал известных моментов — ниже только авто-находки скрипта._\n")
    L.append("---\n")

    # 1. Заблюрить / заменить (экранный слой)
    L.append("## Заблюрить / заменить на экране (слой OCR)\n")
    if v_hits:
        L.append("| Тайминг | Что нашли | Распознано | Действие (решает человек) |")
        L.append("|---|---|---|---|")
        for start, what, frag, end in v_hits:
            tc = hms(start) if end - start < 3 else f"{hms(start)}–{hms(end)}"
            L.append(f"| {tc} | {what} | `{shorten(frag, 50)}` | ? |")
    else:
        L.append("_Находок нет либо окна шеринга не заданы — экранные даты/секреты не отсмотрены._")
    L.append("")

    # 2. Вырезать (речь: заминки + старые цены/офферы)
    L.append("## Вырезать — кандидаты из речи (слой транскрипта)\n")
    rows = []
    for start, text, _ in t_found["glitches"]:
        rows.append((start, "тех. заминка", text))
    for start, text, _ in t_found["prices"]:
        rows.append((start, "цена/оффер в речи", text))
    if rows:
        rows.sort()
        L.append("| Тайминг | Тип | Фраза | Действие (решает человек) |")
        L.append("|---|---|---|---|")
        for start, typ, text in rows:
            L.append(f"| {hms(start)} | {typ} | «{shorten(text)}» | ? |")
    else:
        L.append("_Кандидатов на вырезание в речи не найдено._")
    L.append("")

    # 3. Доп. находки (даты/время в речи — на усмотрение)
    L.append("## Даты и время в речи (на усмотрение автора)\n")
    if t_found["dates"]:
        seen = set()
        L.append("| Тайминг | Нашли | Фраза |")
        L.append("|---|---|---|")
        for start, text, hit in sorted(t_found["dates"]):
            kkey = (round(start), hit)
            if kkey in seen:
                continue
            seen.add(kkey)
            L.append(f"| {hms(start)} | `{shorten(hit, 30)}` | «{shorten(text)}» |")
    else:
        L.append("_Дат/относительного времени в речи не найдено._")
    L.append("")

    # 4. Чистка речи — честная граница
    L.append("## Чистка речи\n")
    L.append("> whisper НЕ размечает кашли/«эээ»/затупы (это аудио, не лексика). "
             "Автоматически не выявлено. Если нужно — отдельным правилом монтажёру: "
             "«речевой мусор почистить на слух».\n")

    # 5. Карта структуры
    L.append("## Карта структуры эфира (для навигации)\n")
    L.append("| Период | Что |")
    L.append("|---|---|")
    L.append(build_map(duration))
    L.append("")

    out_path.write_text("\n".join(L), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Черновик ТЗ на монтаж: транскрипт по всей записи + OCR по окнам шеринга экрана.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--source", required=True, help="Kinescope video_id ИЛИ путь к видеофайлу")
    p.add_argument("--theme", default="efir", help="Тема для имени файла и заголовка")
    p.add_argument("--windows", default="",
                   help="Окна шеринга экрана: '20m-50m,1h15m-1h40m' (или MM:SS/HH:MM:SS)")
    p.add_argument("--whisper-model", default="base", choices=["base", "small", "medium", "large"],
                   help="Модель whisper (дефолт base — быстро, для слабых машин)")
    p.add_argument("--quality", default="480p", help="Качество скачивания из Kinescope (дефолт 480p)")
    p.add_argument("--lang", default="ru", help="Язык аудио для whisper (дефолт ru)")
    p.add_argument("--ocr-lang", default="eng", help="Язык OCR tesseract (дефолт eng; для рус-UI — rus)")
    p.add_argument("--ocr-step", type=int, default=5, help="Шаг OCR-кадров в секундах (дефолт 5)")
    p.add_argument("--frame-step", type=int, default=30, help="Шаг обзорных кадров (дефолт 30)")
    p.add_argument("--out", default="", help="Путь к выходному .md (по умолчанию reports/)")
    p.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR), help="Папка кэша артефактов")
    p.add_argument("--refresh", action="store_true", help="Игнорировать кэш, пересобрать всё")
    p.add_argument("--notes", default="",
                   help="Известные автором моменты (высокая уверенность). Несколько — через ';' "
                        "или перевод строки. Можно с таймкодом в начале: '35:00 цена 990'")
    p.add_argument("--notes-file", default="",
                   help="Файл с заметками автора (по строке на пункт). Объединяется с --notes")
    args = p.parse_args()

    preflight(args)

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    video, key = resolve_source(args, work_dir)
    log(f"📹 Запись: {video.name}")
    duration = probe_duration(video)
    log(f"   длительность: {hms(duration)}")

    notes = load_notes(args)
    if notes:
        log(f"📌 Заметок автора (высокая уверенность): {len(notes)}")

    windows = parse_windows(args.windows) if args.windows else []
    if not windows:
        log("⚠️  Окна шеринга экрана не заданы (--windows). Экранный слой (даты/секреты на "
            "экране) будет пуст. Это главный рычаг скорости — задай окна, где показывали экран.")

    # Слой 1
    srt = transcribe(video, key, args, work_dir)
    t_found = scan_transcript(parse_srt(srt))
    log(f"   речь: даты={len(t_found['dates'])}, цены/офферы={len(t_found['prices'])}, "
        f"заминки={len(t_found['glitches'])}")

    # Слой 2
    v_hits = scan_windows(video, key, windows, args, work_dir) if windows else []
    log(f"   экран: находок={len(v_hits)}")

    out_path = Path(args.out) if args.out else (
        DEFAULT_OUT_DIR / f"{dt.date.today():%Y-%m}_{args.theme}_montazh-tz-draft.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assemble(out_path, video.name if Path(args.source).exists() else f"Kinescope {args.source}",
             duration, windows, t_found, v_hits, args, notes)
    log(f"\n✅ Черновик ТЗ: {out_path}")
    log("   Дальше: проверка video-edit-assistant → сверка с автором → финал.")


if __name__ == "__main__":
    main()
