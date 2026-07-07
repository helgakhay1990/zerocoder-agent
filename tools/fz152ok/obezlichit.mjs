#!/usr/bin/env node
// Обезличивание документа через сервис fz152ok.ru в автоматическом режиме.
// Вся обработка идёт локально в браузере (у сервиса нет сервера для файлов) —
// скрипт лишь автоматизирует клики. Использует системный Google Chrome.
//
// Использование:
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
// Примеры:
//   node obezlichit.mjs ../../docs/dogovor.docx
//   node obezlichit.mjs dogovor.docx --restore --out ./out

import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';

const URL = 'https://fz152ok.ru/';
const CHROME_MAC = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

// ── разбор аргументов ────────────────────────────────────────────────
const argv = process.argv.slice(2);
if (argv.length === 0 || argv.includes('-h') || argv.includes('--help')) {
  console.log(fs.readFileSync(new URL(import.meta.url), 'utf8')
    .split('\n').filter(l => l.startsWith('//')).map(l => l.slice(3)).join('\n'));
  process.exit(0);
}
const opts = { restore: false, headful: false, timeout: 240, out: null, password: null };
const positional = [];
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--restore') opts.restore = true;
  else if (a === '--headful') opts.headful = true;
  else if (a === '--password') opts.password = argv[++i];
  else if (a === '--out') opts.out = argv[++i];
  else if (a === '--timeout') opts.timeout = Number(argv[++i]);
  else positional.push(a);
}

const inputPath = path.resolve(positional[0]);
if (!fs.existsSync(inputPath)) {
  console.error(`✗ Файл не найден: ${inputPath}`);
  process.exit(1);
}
const ext = path.extname(inputPath).toLowerCase().replace('.', '');
const allowed = ['docx', 'doc', 'xlsx', 'pdf', 'txt', 'md'];
if (!allowed.includes(ext)) {
  console.error(`✗ Формат .${ext} не поддерживается. Разрешены: ${allowed.join(', ')}`);
  process.exit(1);
}
if (!fs.existsSync(CHROME_MAC)) {
  console.error(`✗ Не найден Google Chrome по пути ${CHROME_MAC}. Установи Chrome или поправь CHROME_MAC в скрипте.`);
  process.exit(1);
}

const outDir = opts.out ? path.resolve(opts.out) : path.dirname(inputPath);
fs.mkdirSync(outDir, { recursive: true });
if (opts.restore && !opts.password) {
  // человекочитаемый, но стойкий пароль
  opts.password = crypto.randomBytes(12).toString('base64url');
}

const log = (m) => console.log(m);
const timeoutMs = opts.timeout * 1000;

// ── основной сценарий ────────────────────────────────────────────────
const browser = await chromium.launch({
  executablePath: CHROME_MAC,
  headless: !opts.headful,
});
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();

try {
  log(`→ Открываю ${URL}`);
  await page.goto(URL, { waitUntil: 'domcontentloaded' });

  // Закрыть онбординг-модалку (кнопка «Понятно, начать работу»).
  // Она всплывает с задержкой поверх страницы и перехватывает клики,
  // поэтому ждём её появления, а не проверяем наличие сразу.
  const onboarding = page.getByRole('button', { name: /Понятно, начать работу/i });
  try {
    await onboarding.first().waitFor({ state: 'visible', timeout: 12000 });
    await onboarding.first().click();
    await onboarding.first().waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
  } catch { /* модалки нет — например, при повторном визите */ }

  log(`→ Загружаю файл: ${path.basename(inputPath)}`);
  // Основной путь — прямой setInputFiles в скрытый <input type=file>.
  const fileInput = page.locator('input[type=file]').first();
  if (await fileInput.count().catch(() => 0)) {
    await fileInput.setInputFiles(inputPath);
  } else {
    // Запасной путь — через диалог выбора файла.
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: /Выбрать документ/i }).click(),
    ]);
    await chooser.setFiles(inputPath);
  }

  log('→ Жду распознавания (ИИ-модель работает в браузере)…');
  // Готовность: появился блок «Экспорт» и панель «Найденные данные».
  await page.getByText('Найденные данные', { exact: false }).first()
    .waitFor({ state: 'visible', timeout: timeoutMs });
  await page.getByText(/^Экспорт$/).first().waitFor({ state: 'visible', timeout: 30000 });

  // Сколько нашли — для отчёта.
  let found = '?';
  try {
    const badge = page.getByText('Найденные данные', { exact: false }).first();
    found = (await badge.locator('xpath=following-sibling::*[1]').innerText()).trim();
  } catch { /* не критично */ }
  log(`✓ Распознано сущностей: ${found}`);

  const base = path.basename(inputPath, path.extname(inputPath));

  if (!opts.restore) {
    // ── Режим «просто обезличить» ──
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
    const suggested = download.suggestedFilename() || `${base}_obezlichen.${ext}`;
    const dest = path.join(outDir, suggested);
    await download.saveAs(dest);
    log(`\n✓ Готово. Обезличенный файл:\n   ${dest}`);
  } else {
    // ── Режим «с восстановлением» ──
    log('→ Режим: с восстановлением. Настраиваю ключ-карту…');
    await page.getByText('С восстановлением', { exact: false }).first().click();

    // Шаг 1 — пароль.
    const pwd = page.getByRole('textbox', { name: /Введите или сгенерируйте/i }).first();
    await pwd.waitFor({ state: 'visible', timeout: 15000 });
    await pwd.fill(opts.password);

    // Шаг 2 — скачать ключ-карту.
    log('→ Скачиваю ключ-карту…');
    const [keycard] = await Promise.all([
      page.waitForEvent('download', { timeout: 60000 }),
      page.getByRole('button', { name: /Скачать ключ-карту/i }).click(),
    ]);
    const keyName = keycard.suggestedFilename() || `${base}_keycard.json`;
    const keyDest = path.join(outDir, keyName);
    await keycard.saveAs(keyDest);

    // Шаг 3 — скачать документ (становится активным после ключ-карты).
    log('→ Скачиваю обезличенный документ…');
    const docBtn = page.getByRole('button', { name: /Скачать документ/i });
    await docBtn.waitFor({ state: 'visible', timeout: 15000 });
    // дождаться, пока кнопка перестанет быть disabled
    await page.waitForFunction(() => {
      const b = [...document.querySelectorAll('button')].find(x => /Скачать документ/i.test(x.textContent || ''));
      return b && !b.disabled;
    }, { timeout: 30000 }).catch(() => {});
    const [doc] = await Promise.all([
      page.waitForEvent('download', { timeout: 60000 }),
      docBtn.click(),
    ]);
    const docName = doc.suggestedFilename() || `${base}_obezlichen.${ext}`;
    const docDest = path.join(outDir, docName);
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
