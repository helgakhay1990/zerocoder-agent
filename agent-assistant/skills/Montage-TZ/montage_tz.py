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

# Тех. заминки в речи.
# «завис» — только формы про зависание системы (завис/зависло/зависает…), НЕ «зависит/
#   зависимость/независимый»: голый корень давал поток ложных срабатываний (обкатка 22.06).
RE_GLITCH = re.compile(
    r"(?:не\s+открыва|перезагруз|переподключ|вторая\s+попытк|давайте\s+подожд|"
    r"\bзавис(?:ло|ла|ли|ает|нет|ают)?\b|не\s+слышно|не\s+видно|пропал\s+звук|"
    r"техническ|не\s+работает|секунд\w*\s+подожд)",
    re.IGNORECASE,
)

# Взаимодействие спикера с залом: вопросы к аудитории, переклички, Q&A.
# Это навигационный слой — помогает найти прогрев в начале и блок вопросов в конце,
# которые часто режут/двигают на монтаже. Сигналы — обращение к чату, просьба реакций,
# прямые вопросы залу. «плю\w+» ловит и whisper-мисриды «плюсики»→«плютики».
RE_INTERACT = re.compile(
    r"(?:\bв\s+чат\w*|вопрос\w*\s+из\s+чата|"
    r"поставьте\s+плю\w+|плю[сты]\w*ик\w*|поставьте\s+(?:единичк\w*|нолик|ноль|"
    r"семёрочк\w*|семерочк\w*|галочк\w*)|"
    r"как\s+вы\s+думаете|кто\s+из\s+вас|поднимите\s+рук\w*|"
    r"как\s+меня\s+(?:видно|слышно)|как\s+слышно|слышно\s+ли|видно\s+ли|"
    r"задавайте\s+вопрос\w*|задайте\s+вопрос\w*|ваши\s+вопрос\w*|давайте\s+вопрос\w*|"
    r"с\s+какого\s+(?:вы\s+)?города|откуда\s+вы\b|спрашивайте)",
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


def download_video(url: str, tmp: Path, max_attempts: int = 6) -> None:
    """Качает url в tmp, повторяя ПОЛНОЕ скачивание на свежем соединении при обрыве.

    Сервер Kinescope (storage/CDN) НЕ поддерживает Range-запросы — докачать с места
    обрыва нельзя (curl: 33 «doesn't support byte ranges»), `-C -` тут вреден. CDN
    периодически рвёт соединение (наблюдался обрыв ~200 МиБ); лечится не докачкой, а
    повтором целиком на новом соединении — иногда окно «чистое» и файл проходит за раз.
    `--speed-limit/--speed-time` отсекает мёртвый/слишком медленный коннект, чтобы не
    висеть. Если сеть стабильно рвёт во всех попытках — это среда (РФ↔CDN), не баг:
    честно сообщаем и предлагаем локальный файл."""
    for attempt in range(1, max_attempts + 1):
        if tmp.exists():
            tmp.unlink()  # без Range продолжить нельзя — только заново
        rc = run(["curl", "-fL",
                  "--connect-timeout", "20",
                  "--speed-limit", "50000", "--speed-time", "30",
                  "-o", str(tmp), url]).returncode
        if rc == 0:
            return
        got = tmp.stat().st_size // 1048576 if tmp.exists() else 0
        log(f"   ⚠️ обрыв на {got} МиБ (попытка {attempt}/{max_attempts}), "
            f"пробую заново на свежем соединении…")
    die("Скачивание стабильно обрывается — это сеть/CDN (РФ↔Kinescope), не бот. "
        "Передай локальный путь к видео или повтори задачу позже из другой сети.")


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
    # --ocr-lang — это 'rus+eng' (несколько языков через '+'); сверяем каждый по
    # отдельности с установленными, иначе строка 'rus+eng' не найдётся целиком в
    # списке (там по языку на строку) и предупреждение ложно срабатывает даже когда
    # оба языка на месте.
    installed = set(re.findall(r'^[a-z]{3}$', out, re.M))
    missing = [lng for lng in args.ocr_lang.split("+") if lng and lng not in installed]
    if missing:
        log(
            f"⚠️  tesseract: не установлен язык(и) {', '.join(missing)} "
            f"(есть: {', '.join(sorted(installed)) or '—'}).\n"
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
    if chosen is None or not (chosen.get("download_link") or chosen.get("url")):
        die("В ответе Kinescope нет скачиваемого asset с url.")
    # download_link — полная выгрузка (s3/storage, attachment); url — плеерный CDN, режет на 200 МиБ
    dl_url = chosen.get("download_link") or chosen["url"]
    log(f"   качество: {chosen.get('quality')} ({chosen.get('resolution','?')})")

    key = video_id
    out = work_dir / f"{key}_{chosen.get('quality','src')}.mp4"
    if out.exists() and not args.refresh:
        log(f"   кэш: {out.name} (пропускаю скачивание)")
        return out, key
    log("⬇️  Скачиваю запись (это может занять время)...")
    tmp = out.with_name(out.name + ".part")
    download_video(dl_url, tmp)
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
    """Категории речевых находок: (timecode_sec, фраза, что нашли).

    Цены разведены на два потока (обкатка 22.06): сильный сигнал оффера
    (скидка/промокод/акция…) — кандидат на вырезку, реально устаревает; голая
    сумма в речи без оффера — чаще риторика спикера («заработаешь 100 тыс»),
    не цена продукта, поэтому уходит в мягкий слой «на усмотрение автора».
    """
    found = {"dates": [], "offers": [], "prices": [], "glitches": [], "interaction": []}
    for start, _end, text in segs:
        for rx, tag in [(RE_DATE_NUMERIC, "дата"), (RE_DATE_RU_MONTH, "дата"),
                        (RE_REL_TIME, "относит. время"), (RE_YEAR, "год")]:
            for mt in rx.findall(text):
                hit = mt if isinstance(mt, str) else " ".join(mt)
                found["dates"].append((start, text, hit))
        if RE_OFFER.search(text):
            found["offers"].append((start, text, "оффер/дедлайн"))
        elif RE_PRICE.search(text):
            found["prices"].append((start, text, "сумма в речи"))
        if RE_GLITCH.search(text):
            found["glitches"].append((start, text, "тех. заминка"))
        if RE_INTERACT.search(text):
            found["interaction"].append((start, text, "взаимодействие с залом"))
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
            # даты на экране: полные даты, Jun 3 и русское «3 июня 2026»
            # (RE_DATE_RU_MONTH, раз OCR читает rus) ловятся явно.
            for rx in (RE_DATE_NUMERIC, RE_DATE_EN, RE_DATE_RU_MONTH):
                for mt in rx.findall(txt):
                    hit = mt if isinstance(mt, str) else " ".join(mt)
                    hits.append((tc, "дата на экране", hit))
            # Голый год (2023-2039) по умолчанию НЕ ищем — на обкатке 04.06 шумел
            # (мисриды цифр в таблицах). Включается --ocr-years, когда автор прямо
            # просит вычистить годы на слайдах; категория помечена как шумная.
            if args.ocr_years:
                for mt in RE_YEAR.findall(txt):
                    hits.append((tc, "год на экране (шумная категория)", mt))
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


# ─────────────────────────────────────────────────────────────────────────────
# Авто-детект окон шеринга экрана (--windows auto)
# ─────────────────────────────────────────────────────────────────────────────

# «Слово» для оценки плотности текста на кадре: 2+ буквенно-цифровых подряд.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")


def group_dense(scored: list[tuple[float, int]], step: int, min_tokens: int,
                duration: float, bridge_steps: int = 1, pad: float | None = None
                ) -> list[tuple[float, float]]:
    """Из [(timecode_sec, число_токенов), ...] собрать окна «плотного текста».

    Кадр считается экранным, если токенов >= min_tokens. Соседние плотные кадры
    с разрывом <= (bridge_steps+1)*step склеиваются в одно окно (короткие провалы
    OCR между кадрами демонстрации не должны рвать окно). Каждое окно расширяется
    на pad с обеих сторон (по умолчанию step/2 — захватить начало/конец показа,
    который попал между грубыми кадрами), клампится в [0, duration] и сливается
    с перекрывающимися. Одиночный плотный кадр (мелькнувшая дата) сохраняется —
    после паддинга получает ширину. Чистая функция: тестируется без видео."""
    pad = step / 2 if pad is None else pad
    dense = [tc for tc, n in scored if n >= min_tokens]
    if not dense:
        return []
    raw: list[list[float]] = [[dense[0], dense[0]]]
    for tc in dense[1:]:
        if tc - raw[-1][1] <= step * (bridge_steps + 1):
            raw[-1][1] = tc
        else:
            raw.append([tc, tc])
    padded = [(max(0.0, s - pad), min(duration, e + pad)) for s, e in raw]
    padded.sort()
    merged: list[tuple[float, float]] = []
    for s, e in padded:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def detect_windows(video: Path, key: str, args, work_dir: Path) -> list[tuple[float, float]]:
    """Сам находит окна, где показывали экран (терминал/браузер/IDE/приложение).

    Эвристика — плотность распознаваемого текста: талкинг-хед, заставка, пустой
    слайд дают мало токенов; шаринг рабочего экрана — много коротких токенов (UI,
    код, адреса). Грубо семплим кадр раз в `--detect-step` сек (уменьшенный, для
    скорости), OCR на eng, считаем токены, плотные участки склеиваем в окна.
    Ограничение: слайд с большим объёмом текста может попасть в окна — это не
    ошибка, экранный слой только размечает кандидатов, решает человек. Даты на
    слайдах-нарративе он отфильтрует при сверке."""
    frames_dir = work_dir / f"{key}_detect"
    frames_dir.mkdir(parents=True, exist_ok=True)
    log(f"🔍 Авто-детект окон шеринга экрана (кадр раз в {args.detect_step} сек, "
        f"порог {args.detect_min_tokens} токенов)...")
    pattern = str(frames_dir / "d_%05d.jpg")
    run(["ffmpeg", "-i", str(video), "-vf", f"fps=1/{args.detect_step},scale=960:-1",
         "-q:v", "4", pattern, "-y", "-loglevel", "error"])
    frames = sorted(frames_dir.glob("d_*.jpg"))
    scored: list[tuple[float, int]] = []
    for idx, frame in enumerate(frames):
        tc = float(idx * args.detect_step)
        tokens = _TOKEN_RE.findall(ocr_frame(frame, "eng"))
        scored.append((tc, len(tokens)))
    windows = group_dense(scored, args.detect_step, args.detect_min_tokens,
                          probe_duration(video))
    if windows:
        log(f"   найдено окон: {len(windows)} — "
            + ", ".join(f"{hms(s)}–{hms(e)}" for s, e in windows))
    else:
        log("   плотного экранного текста не найдено — экранный слой будет пуст. "
            "Если показ экрана точно был, понизь --detect-min-tokens.")
    return windows


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

def cluster_blocks(times, gap: float = 120):
    """[(start, end, count)] — соседние таймкоды в пределах gap сек считаем одним
    блоком. Превращает россыпь находок в читаемые блоки «с N по M» (вывод урока
    24.06: монтажёру нужны границы блока, а не плоский список реплик)."""
    ts = sorted(times)
    if not ts:
        return []
    blocks = [[ts[0], ts[0], 1]]
    for t in ts[1:]:
        if t - blocks[-1][1] <= gap:
            blocks[-1][1] = t
            blocks[-1][2] += 1
        else:
            blocks.append([t, t, 1])
    return [(a, b, c) for a, b, c in blocks]


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение по числу: 1 реплика / 2 реплики / 5 реплик."""
    nn = abs(n) % 100
    d = nn % 10
    if 11 <= nn <= 14 or d == 0 or d >= 5:
        return many
    if d == 1:
        return one
    return few


def build_map(duration: float, windows, t_found) -> str:
    """Карта-навигация из РЕАЛЬНЫХ якорей (а не пустая сетка): показ экрана,
    блоки работы с залом, офферы/дедлайны. Тематические подписи — за человеком
    (video-edit-assistant), но скелет с таймкодами скрипт даёт сам."""
    anchors = []  # (start, label)
    for a, b in (windows or []):
        anchors.append((a, f"🖥 показ экрана {hms(a)}–{hms(b)}"))
    for a, b, c in cluster_blocks([s for s, _, _ in t_found.get("interaction", [])]):
        rng = hms(a) if a == b else f"{hms(a)}–{hms(b)}"
        anchors.append((a, f"🙋 работа с залом {rng} ({c} {_plural(c, 'реплика', 'реплики', 'реплик')})"))
    for s, _, _ in t_found.get("offers", []):
        anchors.append((s, f"💰 оффер/дедлайн в речи {hms(s)}"))
    if not anchors:
        return "| — | якорей не найдено (нет окон экрана / взаимодействия / офферов) |"
    rows = [f"| {hms(s)} | {label} |" for s, label in sorted(anchors)]
    return "\n".join(rows)


def shorten(text: str, n: int = 90) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def assemble(out_path: Path, src_label, duration, windows, t_found, v_hits, args,
             notes=None, auto_windows=False) -> None:
    notes = notes or []
    d = dt.date.today().isoformat()
    L = []
    L.append(f"# ТЗ на монтаж — {args.theme} (ЧЕРНОВИК)\n")
    L.append("> ⚠️ **ЧЕРНОВИК, собран автоматически.** Перед отдачей монтажёру — проверка "
             "`video-edit-assistant` и сверка с автором. Решения «резать/блюрить/оставить» "
             "принимает человек, скрипт только размечает кандидатов.\n")
    L.append(f"**Источник:** {src_label}")
    L.append(f"**Длительность:** {hms(duration)}")
    win_note = ""
    if not windows:
        win_note = " — ⚠️ окна не заданы, экранный слой пуст"
    elif auto_windows:
        win_note = (" (🔍 найдены авто-детектом по плотности текста — "
                    "сверь границы и отсей слайды-нарратив)")
    L.append(f"**Окон шеринга экрана отсканировано:** {len(windows)}"
             + win_note)
    if windows and auto_windows:
        L.append("**Окна (авто):** "
                 + "; ".join(f"{hms(s)}–{hms(e)}" for s, e in windows))
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

    # 0. Взаимодействие с залом (навигация: прогрев в начале, Q&A в конце)
    L.append("## Взаимодействие с аудиторией (блок вопросов к залу)\n")
    L.append("> Где спикер работает с залом — задаёт вопросы, просит реакции в чат, "
             "отвечает на вопросы. Навигационный слой: прогрев в начале и Q&A в конце "
             "часто двигают/режут. Границы блоков уточни на записи.\n")
    inter = sorted(t_found["interaction"])
    if inter:
        blocks = cluster_blocks([s for s, _, _ in inter])
        L.append("**Блоки** (соседние реплики ближе 2 мин = один блок — это и есть границы для монтажа):\n")
        L.append("| Блок | Реплик | Первая реплика блока |")
        L.append("|---|---|---|")
        for a, b, c in blocks:
            rng = hms(a) if a == b else f"{hms(a)}–{hms(b)}"
            sample = next((shorten(t, 60) for s, t, _ in inter if a <= s <= b), "")
            L.append(f"| {rng} | {c} | «{sample}» |")
        L.append("\nВсе реплики по таймкодам:")
        for start, text, _ in inter:
            L.append(f"- `{hms(start)}` «{shorten(text)}»")
    else:
        L.append("_Явных сигналов работы с залом не найдено (или их не было в речи)._")
    L.append("")

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

    # 2. Вырезать (речь: заминки + офферы/дедлайны — то, что реально устаревает)
    L.append("## Вырезать — кандидаты из речи (слой транскрипта)\n")
    rows = []
    for start, text, _ in t_found["glitches"]:
        rows.append((start, "тех. заминка", text))
    for start, text, _ in t_found["offers"]:
        rows.append((start, "оффер/дедлайн в речи", text))
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

    # 3b. Суммы в речи — мягкий слой (часто риторика «заработаешь N», не цена продукта)
    L.append("## Суммы в речи (на усмотрение автора)\n")
    L.append("> Голые суммы без сигнала оффера. Обычно это мотивация спикера "
             "(«заработаете 200 тыс»), а не цена продукта/тариф — но сверь, не "
             "проскочила ли реальная цена, которая со временем устареет.\n")
    if t_found["prices"]:
        L.append("| Тайминг | Фраза |")
        L.append("|---|---|")
        for start, text, _ in sorted(t_found["prices"]):
            L.append(f"| {hms(start)} | «{shorten(text)}» |")
    else:
        L.append("_Сумм в речи не найдено._")
    L.append("")

    # 4. Чистка речи — честная граница
    L.append("## Чистка речи\n")
    L.append("> whisper НЕ размечает кашли/«эээ»/затупы (это аудио, не лексика). "
             "Автоматически не выявлено. Если нужно — отдельным правилом монтажёру: "
             "«речевой мусор почистить на слух».\n")

    # 5. Карта структуры — якоря из находок (показ экрана / работа с залом / офферы)
    L.append("## Карта структуры эфира (навигация)\n")
    L.append("> Якоря из автонаходок. Тематические подписи блоков добавляет человек "
             "(`video-edit-assistant`) — скелет с таймкодами скрипт даёт сам.\n")
    L.append("| Тайминг | Якорь |")
    L.append("|---|---|")
    L.append(build_map(duration, windows, t_found))
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
                   help="Окна шеринга экрана: '20m-50m,1h15m-1h40m' (или MM:SS/HH:MM:SS); "
                        "'auto' — найти окна автоматически по плотности текста на экране; "
                        "пусто — экранный слой пропустить")
    p.add_argument("--detect-step", type=int, default=30,
                   help="Шаг грубых кадров при авто-детекте окон, сек (дефолт 30)")
    p.add_argument("--detect-min-tokens", type=int, default=25,
                   help="Порог токенов на кадр, выше которого кадр считается показом экрана "
                        "(дефолт 25; ниже — чувствительнее, больше слайдов в выдаче)")
    p.add_argument("--whisper-model", default="base", choices=["base", "small", "medium", "large"],
                   help="Модель whisper (дефолт base — быстро, для слабых машин)")
    p.add_argument("--quality", default="480p", help="Качество скачивания из Kinescope (дефолт 480p)")
    p.add_argument("--lang", default="ru", help="Язык аудио для whisper (дефолт ru)")
    p.add_argument("--ocr-lang", default="rus+eng",
                   help="Язык OCR tesseract (дефолт rus+eng — ловит и русские даты на "
                        "экране, и латиницу/терминал; для чисто-англ. UI можно eng)")
    p.add_argument("--ocr-years", action="store_true",
                   help="Искать на экране и голые годы (2023-2039), не только полные "
                        "даты. Включай, когда автор просит вычистить годы на слайдах — "
                        "даёт больше шума (мисриды цифр в таблицах), но ловит «2026»/«2030».")
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

    auto_windows = args.windows.strip().lower() in ("auto", "авто")
    if auto_windows:
        windows = detect_windows(video, key, args, work_dir)
    elif args.windows:
        windows = parse_windows(args.windows)
    else:
        windows = []
        log("⚠️  Окна шеринга экрана не заданы (--windows). Экранный слой (даты/секреты на "
            "экране) будет пуст. Задай окна вручную или '--windows auto' для авто-детекта.")

    # Слой 1
    srt = transcribe(video, key, args, work_dir)
    t_found = scan_transcript(parse_srt(srt))
    log(f"   речь: даты={len(t_found['dates'])}, офферы={len(t_found['offers'])}, "
        f"суммы={len(t_found['prices'])}, "
        f"заминки={len(t_found['glitches'])}, "
        f"взаимодействие={len(t_found['interaction'])}")

    # Слой 2
    v_hits = scan_windows(video, key, windows, args, work_dir) if windows else []
    log(f"   экран: находок={len(v_hits)}")

    out_path = Path(args.out) if args.out else (
        DEFAULT_OUT_DIR / f"{dt.date.today():%Y-%m}_{args.theme}_montazh-tz-draft.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assemble(out_path, video.name if Path(args.source).exists() else f"Kinescope {args.source}",
             duration, windows, t_found, v_hits, args, notes, auto_windows)
    log(f"\n✅ Черновик ТЗ: {out_path}")
    log("   Дальше: проверка video-edit-assistant → сверка с автором → финал.")


if __name__ == "__main__":
    main()
