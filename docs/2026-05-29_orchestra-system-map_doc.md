# Карта системы: агент-ассистент Zerocoder с оркестрацией

**Дата:** 2026-05-29 (обновлено 2026-06-03: архитектура запуска уровней) · **Назначение:** одностраничная карта оркестра для презентации плана развития и быстрого ввода в систему. Что из чего состоит, кто кого запускает, откуда берёт правду.

Состав на дату: **главный оркестратор + 15 субагентов** (3 уровня, +`landing-auditor` с 02.06) · **3 скилла-методички** · **4 файла-источника правды** · **5 чек-листов качества** · **библиотека образцов `references/`**.

---

## Три уровня оркестра

```
┌─────────────────────────────────────────────────────────────────────┐
│  ГЛАВНЫЙ ОРКЕСТРАТОР  (Claude по agent-assistant/CLAUDE.md)           │
│  Диалог с Повелительницей · маршрутизация · ЗАПУСКАЕТ ВСЕ субагенты   │
│  (и 2-й, и 3-й уровень) · собирает пакеты · ведёт _state.md           │
└──┬───────────────────────────────────────────────────────────────┬───┘
   │ (1) запускает подмастерьев напрямую          (3) передаёт сырьё │
   │     (параллельно, где можно)                  оркестратору 2-й  │
   ▼                                                                 ▼
┌──────────────────────────────────────────┐   ┌──────────────────────────┐
│  ПОДМАСТЕРЬЯ — 3-я ступень (sonnet)        │   │  СБОРЩИКИ — 2-я ст. (opus) │
│  audience-researcher · offer-mechanic      │──▶│  launch-strategist         │
│  data-collector · comparison-builder ·     │   │  analyst-assistant         │
│   insight-extractor                        │   │  video-edit-assistant      │
│  transcript-analyzer · visual-scanner      │   │  (2) собирают из сырья,    │
│  → возвращают сырьё ГЛАВНОМУ оркестратору   │   │   возвращают текст ему     │
└──────────────────────────────────────────┘   └──────────────────────────┘

ИСПОЛНИТЕЛИ без подмастерьев (sonnet), вызываются главным напрямую:
   announce-writer · landing-architect · landing-auditor · methodist
ФИНАЛЬНЫЙ ПРОХОД:
   editor — вычитывает ЛЮБОЙ готовый текст перед отдачей Повелительнице
```

**Правило уровней (обновлено 2026-06-03):** субагент не умеет запускать другой субагент (нет инструмента запуска внутри субагента — проверено боевым прогоном). Поэтому **ВСЕ субагенты — и подмастерьев 3-й ступени, и сборщиков 2-й — запускает главный оркестратор.** Сборщик 2-й ступени (`launch-strategist`, `analyst-assistant`, `video-edit-assistant`) сам подмастерьев не вызывает: он получает их выход как вход от главного оркестратора (или для лёгкой задачи делает их работу в своём контексте). Подмастерья не общаются с Повелительницей и не пишут файлы — возвращают текст главному, файлы сохраняет он. На opus работают три «тяжёлых» сборщика, остальные — на sonnet.

---

## Таблица маршрутизации

| Запрос Повелительницы (триггеры) | Субагент | Подмастерья | Источники правды | Скилл / чек-лист |
|---|---|---|---|---|
| анонс, письмо, пост в тг, прехедер | **announce-writer** | — | zerocoder-facts, audience, **speakers**, tone-of-voice, references/announces | AI-Copywriter · announce-quality |
| лендинг, структура страницы, блоки, по референсу (СТРОИТЬ) | **landing-architect** | — | zerocoder-facts, references/landings | Image-Prompt · landing-quality |
| проверь страницу/лендинг, аудит, что не так, проверь ссылки (ПРОВЕРЯТЬ) | **landing-auditor** | — | zerocoder-facts, checklists/landing-quality | landing-quality |
| концепция, запуск, стратегия, механика, воронка | **launch-strategist** | audience-researcher, offer-mechanic | zerocoder-facts, references/concepts | concept-quality |
| гайд, презентация, слайды, инструкция | **methodist** | — | zerocoder-facts, **speakers**, references/decks, references/guides | Image-Prompt · deck/guide-quality |
| конкуренты, сравни, аудит, рынок, что у них | **analyst-assistant** | data-collector, comparison-builder, insight-extractor | zerocoder-facts | скилл ai-competitors |
| монтаж, ТЗ монтажёру, вырезать, отсмотри запись | **video-edit-assistant** | transcript-analyzer, visual-scanner | — (работает с записью + _state.md) | montazh-checklist |
| любой готовый текст перед отдачей | **editor** | — | CLAUDE.md, tone-of-voice, нужный чек-лист | все *-quality |

---

## Источники правды (никто ничего не выдумывает)

| Файл | Что внутри | Кто обращается |
|---|---|---|
| `knowledge-base/zerocoder-facts.md` | продукты, тарифы, оффер, § 7 активная воронка | почти все контентные + аналитика |
| `knowledge-base/audience.md` | сегменты ЦА, боли, язык | audience-researcher, announce-writer |
| `knowledge-base/speakers.md` | регалии спикеров, как представлять | announce-writer, methodist |
| `knowledge-base/tone-of-voice.md` | ToV, регистры Zerocoder / IU | editor, announce-writer |
| `references/<жанр>/` | эталонные образцы по жанрам | landing-architect, announce-writer, launch-strategist, methodist |
| `checklists/*.md` | критерии качества по жанрам | editor + самопроверка каждого |

**Если файла/факта нет** — субагент спрашивает Повелительницу, не заполняет догадкой.

## Скиллы-методички

| Скилл | Чей инструмент | Зачем |
|---|---|---|
| `AI-Copywriter` | announce-writer | один источник → тексты под все каналы |
| `Image-Prompt` | landing-architect, methodist | промпты под визуал |
| `Webinar-Prep` | главный оркестратор, methodist | операционная готовность эфира |

---

## Конвейерные сценарии

**Запуск вебинара:** launch-strategist → landing-architect → announce-writer ×N → methodist (презентация + гайд) → Webinar-Prep → *(после эфира)* video-edit-assistant. Финал каждого текста — editor.

**Акция к дате:** analyst-assistant → launch-strategist → landing-architect → announce-writer ×N → editor.

**Интенсив:** аналитика → концепция → лендинг → анонсы прогрева → гайд → презентации модулей → постмонтаж. Каждый этап — отдельный шаг в `_state.md`.

**Монтаж записи:** video-edit-assistant (transcript-analyzer + visual-scanner) → ТЗ на монтаж → сверка с Повелительницей.

Под каждый запуск копируется `agent-assistant/_state-template.md` → `_state.md`.

---

## Сквозные принципы

- **Правда — только из knowledge-base.** Нет данных — открытый вопрос, не выдумка.
- **Регистр по бренду решает раньше сегмента** — Zerocoder разговорный / IU академический (логика в `editor`).
- **editor — обязательный финал** любого текста перед Повелительницей.
- **Подмастерья молчат с Повелительницей** — общаются только через оркестратора.
- **Галочка в `_state.md`** ставится только после реальной верификации артефакта.
- **Ничего во внешние каналы** — только черновики на проверку.

---

## Распространение

Система упакована для передачи коллегам и опубликована в **приватном** GitHub-репозитории `helgakhay1990/zerocoder-agent`.

- **Что передаётся:** субагенты, скиллы, knowledge-base, чек-листы, references, шаблон `_state`, эта карта, `SETUP.md`, `.mcp.json.example`.
- **Что НЕ уходит** (через `.gitignore`): живые ключи (`.mcp.json`), личные настройки, память агентов, записи и расшифровки эфиров, скриншоты, тяжёлые медиа.
- **Подключение коллеги:** `git clone` → `cp agent-assistant/.mcp.json.example agent-assistant/.mcp.json` (вписать свои ключи) → открыть в Claude Code → наполнить `knowledge-base/` под свою компанию. Шаги — в `SETUP.md`.
