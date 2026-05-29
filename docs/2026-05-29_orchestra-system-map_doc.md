# Карта системы: агент-ассистент Zerocoder с оркестрацией

**Дата:** 2026-05-29 · **Назначение:** одностраничная карта оркестра для презентации плана развития и быстрого ввода в систему. Что из чего состоит, кто кому делегирует, откуда берёт правду.

Состав на дату: **главный оркестратор + 14 субагентов** (3 уровня) · **3 скилла-методички** · **4 файла-источника правды** · **5 чек-листов качества** · **библиотека образцов `references/`**.

---

## Три уровня оркестра

```
┌─────────────────────────────────────────────────────────────────────┐
│  ГЛАВНЫЙ ОРКЕСТРАТОР  (Claude по agent-assistant/CLAUDE.md)           │
│  Диалог с Повелительницей · маршрутизация · сборка финальных пакетов  │
│  · ведёт _state.md · лёгкие задачи без отдельного субагента           │
└───────────────┬───────────────────────────────────────────────────────┘
                │ делегирует по триггерам
   ┌────────────┼───────────────────────────┬──────────────────────────┐
   ▼            ▼                            ▼                          ▼
┌─────────┐ ┌──────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│ 2-я ст. │ │ launch-strategist│ │ analyst-assistant    │ │ video-edit-assistant │
│ (opus)  │ │ концепции запуска│ │ конкуренты/аудиты    │ │ ТЗ на монтаж         │
└─────────┘ └───────┬──────────┘ └──────────┬───────────┘ └──────────┬───────────┘
                    ▼ подмастерья           ▼ подмастерья            ▼ подмастерья
            ┌───────────────┐      ┌──────────────────┐      ┌──────────────────┐
   3-я ст.  │ audience-     │      │ data-collector   │      │ transcript-      │
   (sonnet) │  researcher   │      │ comparison-      │      │  analyzer        │
            │ offer-mechanic│      │  builder         │      │ visual-scanner   │
            └───────────────┘      │ insight-extractor│      └──────────────────┘
                                   └──────────────────┘

ИСПОЛНИТЕЛИ без подмастерьев (sonnet), вызываются главным напрямую:
   announce-writer · landing-architect · methodist
ФИНАЛЬНЫЙ ПРОХОД:
   editor — вычитывает ЛЮБОЙ готовый текст перед отдачей Повелительнице
```

**Правило уровней:** подмастерья (3-я ступень) никогда не запускаются напрямую и не общаются с Повелительницей — только через своего оркестратора 2-й ступени. На opus работают три «тяжёлых» оркестратора (`launch-strategist`, `analyst-assistant`, `video-edit-assistant`), остальные — на sonnet.

---

## Таблица маршрутизации

| Запрос Повелительницы (триггеры) | Субагент | Подмастерья | Источники правды | Скилл / чек-лист |
|---|---|---|---|---|
| анонс, письмо, пост в тг, прехедер | **announce-writer** | — | zerocoder-facts, audience, **speakers**, tone-of-voice, references/announces | AI-Copywriter · announce-quality |
| лендинг, структура страницы, блоки, по референсу | **landing-architect** | — | zerocoder-facts, references/landings | Image-Prompt · landing-quality |
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
