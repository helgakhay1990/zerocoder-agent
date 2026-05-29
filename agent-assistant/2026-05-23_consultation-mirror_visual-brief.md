# Визуальный бриф — шапка письма
**Кампания:** акция «5 мест на консультацию», 2026-05-23
**Концепция:** провокация / зеркало
**Парный документ:** `2026-05-23_consultation-mirror_announce.md`

---

## Назначение
Баннер-шапка email-письма. В тг-боте по задаче картинку не используем.

## Формат
- Соотношение: 2:1
- Размер: 1200×600 px
- Стиль: редакционная иллюстрация в духе The New Yorker / The Atlantic
- Левая треть кадра свободна под наложение заголовка дизайнером

## Идея кадра
Узнаваемая сцена «застрял на этапе намерения»: открытый ноутбук, на экране — хаос вкладок (без читаемых брендов), рядом остывшая чашка, закрытый блокнот с ручкой. Холодноватый свет, приглушённая палитра. Никого в кадре — только следы намерения, которое не сдвинулось.

---

## Промпт под Flux (English, естественный язык)

```
Editorial illustration: a laptop on a wooden desk, seen from a slightly elevated three-quarter angle. The laptop screen is filled with a chaotic mosaic of browser tabs — small unreadable text fragments hinting at AI topics, none resembling recognizable brand names. A ceramic mug of cold coffee sits next to the laptop, untouched. A closed notebook with a pen on top lies beside it. Soft late-afternoon light enters from the left, slightly cool color temperature. Muted palette of warm beige and cool gray, with a single quiet accent of dim amber. Composition leaves the entire left third of the frame empty for a headline overlay. Style: contemporary editorial illustration in the spirit of The New Yorker or The Atlantic — clean restrained linework, limited color, no photorealism, no glossy 3D render, no stock photography feel. Mood: quiet, slightly somber, immediately recognizable as an everyday scene of unfinished intent. Horizontal banner, aspect ratio 2:1.
```

## Negative prompt

```
photorealism, glossy 3D render, recognizable brand logos, readable text on screen, person in frame, hands, faces, stock pose, lightbulb idea symbol, hyper-saturated colors, neon, gradients, low quality, blurry, artifacts, watermarks, signature
```

---

## Параметры под Replicate (Flux)

| Параметр | Значение |
|---|---|
| Модель | `black-forest-labs/flux-dev` (выше качество) или `flux-schnell` (быстрее/дешевле) |
| `aspect_ratio` | `2:1` |
| `output_format` | `png` |
| `output_quality` | `90+` |
| `num_outputs` | `1–2` (для выбора) |

## Дальше
Промпт собран. Жду подтверждения, **Повелительница** — запускаю генерацию через Replicate MCP (Flux dev или schnell — что берём?) или сначала ждём правок по тексту/концепции.
