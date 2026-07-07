#!/usr/bin/env node
// Обезличивание и восстановление документов через сервис fz152ok.ru.
// Вся обработка идёт локально в браузере (у сервиса нет сервера для файлов) —
// скрипт лишь автоматизирует клики. Использует системный Google Chrome.
//
// ОБЕЗЛИЧИВАНИЕ:
//   node obezlichit.mjs <файл> [--restore] [--password <пароль>] [--out <папка>] [--headful] [--timeout <сек>]
//
//   <файл>            путь к документу (docx, doc, xlsx, pdf, txt, md)
//   --restore         режим «с восстановлением»: помимо обезличенного файла
//                     сохраняет зашифрованную ключ-карту (нужен пароль)
//   --password <...>  пароль для ключ-карты (по умолчанию генерируется случайный,
//                     печатается в консоль — сохрани его, без него не восстановить)
//   --out <папка>     куда класть результат (по умолчанию — папка исходного файла)
//   --headful         показать окно браузера (по умолчанию — фоновый режим)
//   --timeout <сек>   ожидание распознавания, по умолчанию 240 (первый запуск
//                     качает ИИ-модель ~170 МБ, дальше она в кэше и быстрее)
//
// ВОССТАНОВЛЕНИЕ (обратная замена меток на оригинальные данные по ключ-карте):
//   node obezlichit.mjs restore <документ> <ключ-карта.json> --password <пароль> [--out <папка>] [--headful]
//
//   <документ>        обезличенный файл (или ответ ИИ с метками вида [ФИО_1])
//   <ключ-карта.json> ключ-карта, полученная при обезличивании с --restore
//   --password <...>  пароль от ключ-карты (обязателен)
//
// Примеры:
//   node obezlichit.mjs ../../docs/dogovor.docx
//   node obezlichit.mjs dogovor.docx --restore --out ./out
//   node obezlichit.mjs restore ответ_ии.docx dogovor_ключ_карта.json --password TestPass123

import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';

const SERVICE_URL = 'https://fz152ok.ru/';
const CHROME_MAC = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const ALLOWED = ['docx', 'doc', 'xlsx', 'pdf', 'txt', 'md'];

// ── разбор аргументов ────────────────────────────────────────────────
const argv = process.argv.slice(2);
if (argv.length === 0 || argv.includes('-h') || argv.includes('--help')) {
  console.log(fs.readFileSync(new URL(import.meta.url), 'utf8')
    .split('\n').filter(l => l.startsWith('//')).map(l => l.slice(3)).join('\n'));
  process.exit(0);
}

// Режим определяется первым словом: `restore` → восстановление, иначе обезличивание.
const mode = argv[0] === 'restore' ? 'restore' : 'obezlichit';
const rest = mode === 'restore' ? argv.slice(1) : argv;

const opts = { restore: false, headful: false, timeout: 240, out: null, password: null };
const positional = [];
for (let i = 0; i < rest.length; i++) {
  const a = rest[i];
  if (a === '--restore') opts.restore = true;
  else if (a === '--headful') opts.headful = true;
  else if (a === '--password') opts.password = rest[++i];
  else if (a === '--out') opts.out = rest[++i];
  else if (a === '--timeout') opts.timeout = Number(rest[++i]);
  else positional.push(a);
}

const die = (msg) => { console.error(`✗ ${msg}`); process.exit(1); };
const checkExt = (p) => {
  const e = path.extname(p).toLowerCase().replace('.', '');
  if (!ALLOWED.includes(e)) die(`Формат .${e} не поддерживается. Разрешены: ${ALLOWED.join(', ')}`);
  return e;
};
if (!fs.existsSync(CHROME_MAC))
  die(`Не найден Google Chrome по пути ${CHROME_MAC}. Установи Chrome или поправь CHROME_MAC в скрипте.`);

const log = (m) => console.log(m);
const timeoutMs = opts.timeout * 1000;

// Валидация входа под конкретный режим.
let docPath, keyPath, ext;
if (mode === 'restore') {
  if (positional.length < 2) die('Восстановление: нужны два файла — документ и ключ-карта. См. --help.');
  docPath = path.resolve(positional[0]);
  keyPath = path.resolve(positional[1]);
  if (!fs.existsSync(docPath)) die(`Документ не найден: ${docPath}`);
  if (!fs.existsSync(keyPath)) die(`Ключ-карта не найдена: ${keyPath}`);
  if (!opts.password) die('Восстановление невозможно без пароля. Укажи --password <пароль>.');
  ext = checkExt(docPath);
} else {
  if (positional.length < 1) die('Укажи путь к документу. См. --help.');
  docPath = path.resolve(positional[0]);
  if (!fs.existsSync(docPath)) die(`Файл не найден: ${docPath}`);
  ext = checkExt(docPath);
  if (opts.restore && !opts.password) opts.password = crypto.randomBytes(12).toString('base64url');
}

const outDir = opts.out ? path.resolve(opts.out) : path.dirname(docPath);
fs.mkdirSync(outDir, { recursive: true });
const base = path.basename(docPath, path.extname(docPath));

// ── запуск браузера ──────────────────────────────────────────────────
const browser = await chromium.launch({ executablePath: CHROME_MAC, headless: !opts.headful });
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();

// Закрыть онбординг-модалку (всплывает с задержкой и перехватывает клики).
async function dismissOnboarding() {
  const btn = page.getByRole('button', { name: /Понятно, начать работу/i });
  try {
    await btn.first().waitFor({ state: 'visible', timeout: 12000 });
    await btn.first().click();
    await btn.first().waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
  } catch { /* модалки нет — например, при повторном визите */ }
}

try {
  log(`→ Открываю ${SERVICE_URL}`);
  await page.goto(SERVICE_URL, { waitUntil: 'domcontentloaded' });
  await dismissOnboarding();

  if (mode === 'restore') {
    // ═══ ВОССТАНОВЛЕНИЕ ═══
    log('→ Открываю вкладку «Восстановить»');
    await page.getByRole('button', { name: /^Восстановить$/i }).first().click();
    // Дожидаемся именно панели восстановления — её подписанные зоны загрузки
    // есть только здесь. Заодно это гарантирует, что вкладка переключилась.
    const docZone = page.getByRole('button', { name: /Ответ ИИ/i }).first();
    const keyZone = page.getByRole('button', { name: /Ключ-карта/i }).first();
    await docZone.waitFor({ state: 'visible', timeout: 15000 });

    // Грузим через сами зоны (диалог выбора файла), а НЕ по глобальному
    // input[accept]: на странице живёт и скрытый input вкладки обезличивания,
    // и попадание в него переключает сайт в обезличивание.
    log(`→ Загружаю документ: ${path.basename(docPath)}`);
    const [ch1] = await Promise.all([page.waitForEvent('filechooser'), docZone.click()]);
    await ch1.setFiles(docPath);
    log(`→ Загружаю ключ-карту: ${path.basename(keyPath)}`);
    const [ch2] = await Promise.all([page.waitForEvent('filechooser'), keyZone.click()]);
    await ch2.setFiles(keyPath);

    log('→ Ввожу пароль');
    await page.getByRole('textbox', { name: /Пароль от ключ-карты/i }).first().fill(opts.password);

    const restoreBtn = page.getByRole('button', { name: /Восстановить оригинал/i }).first();
    // Кнопка активируется, когда заполнены оба файла и пароль.
    await page.waitForFunction(() => {
      const b = [...document.querySelectorAll('button')]
        .find(x => /Восстановить оригинал/i.test(x.textContent || ''));
      return b && !b.disabled;
    }, { timeout: 30000 });

    log('→ Восстанавливаю оригинал…');
    await restoreBtn.click();
    // Результат либо скачивается сам, либо появляется кнопка «Скачать» —
    // ловим оба варианта.
    const download = await Promise.race([
      page.waitForEvent('download', { timeout: 60000 }),
      (async () => {
        const dl = page.getByRole('button', { name: /Скачать/i }).first();
        await dl.waitFor({ state: 'visible', timeout: 60000 });
        const [d] = await Promise.all([page.waitForEvent('download'), dl.click()]);
        return d;
      })(),
    ]);

    const name = download.suggestedFilename() || `${base}_восстановлен.${ext}`;
    const dest = path.join(outDir, name);
    await download.saveAs(dest);
    log(`\n✓ Готово. Восстановленный документ:\n   ${dest}`);

  } else if (!opts.restore) {
    // ═══ ОБЕЗЛИЧИВАНИЕ (просто обезличить) ═══
    log(`→ Загружаю файл: ${path.basename(docPath)}`);
    const fileInput = page.locator('input[type=file]').first();
    if (await fileInput.count().catch(() => 0)) {
      await fileInput.setInputFiles(docPath);
    } else {
      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.getByRole('button', { name: /Выбрать документ/i }).click(),
      ]);
      await chooser.setFiles(docPath);
    }

    log('→ Жду распознавания (ИИ-модель работает в браузере)…');
    await page.getByText('Найденные данные', { exact: false }).first()
      .waitFor({ state: 'visible', timeout: timeoutMs });
    await page.getByText(/^Экспорт$/).first().waitFor({ state: 'visible', timeout: 30000 });
    let found = '?';
    try {
      const badge = page.getByText('Найденные данные', { exact: false }).first();
      found = (await badge.locator('xpath=following-sibling::*[1]').innerText()).trim();
    } catch { /* не критично */ }
    log(`✓ Распознано сущностей: ${found}`);

    log('→ Режим: просто обезличить. Скачиваю обезличенный файл…');
    // Карточка «Быстрое скачивание» — выбор режима; скачивание запускает
    // отдельная кнопка «Скачать документ» под ней.
    await page.getByText('Быстрое скачивание', { exact: false }).first().click();
    const dlBtn = page.getByRole('button', { name: /Скачать документ/i }).first();
    await dlBtn.waitFor({ state: 'visible', timeout: 15000 });
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 60000 }),
      dlBtn.click(),
    ]);
    const dest = path.join(outDir, download.suggestedFilename() || `${base}_обезличен.${ext}`);
    await download.saveAs(dest);
    log(`\n✓ Готово. Обезличенный файл:\n   ${dest}`);

  } else {
    // ═══ ОБЕЗЛИЧИВАНИЕ (с восстановлением: файл + ключ-карта) ═══
    log(`→ Загружаю файл: ${path.basename(docPath)}`);
    const fileInput = page.locator('input[type=file]').first();
    if (await fileInput.count().catch(() => 0)) {
      await fileInput.setInputFiles(docPath);
    } else {
      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.getByRole('button', { name: /Выбрать документ/i }).click(),
      ]);
      await chooser.setFiles(docPath);
    }

    log('→ Жду распознавания (ИИ-модель работает в браузере)…');
    await page.getByText('Найденные данные', { exact: false }).first()
      .waitFor({ state: 'visible', timeout: timeoutMs });
    await page.getByText(/^Экспорт$/).first().waitFor({ state: 'visible', timeout: 30000 });
    let found = '?';
    try {
      const badge = page.getByText('Найденные данные', { exact: false }).first();
      found = (await badge.locator('xpath=following-sibling::*[1]').innerText()).trim();
    } catch { /* не критично */ }
    log(`✓ Распознано сущностей: ${found}`);

    log('→ Режим: с восстановлением. Настраиваю ключ-карту…');
    await page.getByText('С восстановлением', { exact: false }).first().click();

    const pwd = page.getByRole('textbox', { name: /Введите или сгенерируйте/i }).first();
    await pwd.waitFor({ state: 'visible', timeout: 15000 });
    await pwd.fill(opts.password);

    log('→ Скачиваю ключ-карту…');
    const [keycard] = await Promise.all([
      page.waitForEvent('download', { timeout: 60000 }),
      page.getByRole('button', { name: /Скачать ключ-карту/i }).click(),
    ]);
    const keyDest = path.join(outDir, keycard.suggestedFilename() || `${base}_keycard.json`);
    await keycard.saveAs(keyDest);

    log('→ Скачиваю обезличенный документ…');
    const docBtn = page.getByRole('button', { name: /Скачать документ/i });
    await docBtn.waitFor({ state: 'visible', timeout: 15000 });
    await page.waitForFunction(() => {
      const b = [...document.querySelectorAll('button')].find(x => /Скачать документ/i.test(x.textContent || ''));
      return b && !b.disabled;
    }, { timeout: 30000 }).catch(() => {});
    const [doc] = await Promise.all([
      page.waitForEvent('download', { timeout: 60000 }),
      docBtn.click(),
    ]);
    const docDest = path.join(outDir, doc.suggestedFilename() || `${base}_обезличен.${ext}`);
    await doc.saveAs(docDest);

    log(`\n✓ Готово. Сохранено:`);
    log(`   Обезличенный файл: ${docDest}`);
    log(`   Ключ-карта:        ${keyDest}`);
    log(`   Пароль:            ${opts.password}`);
    log(`\n⚠ Пароль нигде больше не хранится. Без него ключ-карта бесполезна — сохрани его.`);
  }
} catch (err) {
  console.error(`\n✗ Ошибка: ${err.message}`);
  const shot = path.join(outDir, 'fz152ok_error.png');
  await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
  console.error(`  Скриншот состояния: ${shot}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
