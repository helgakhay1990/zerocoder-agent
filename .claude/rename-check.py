#!/usr/bin/env python3
"""
Hook: проверяет имена файлов при создании/копировании в рабочую директорию.
Формула: [дата]_[тема]_[тип-файла].[расширение]
Примеры: 2026-05_webinar-anons_email.md, client-export_report.csv, fns-guide_doc.md
"""
import json
import sys
import os
import re

data = json.load(sys.stdin)
tool_name = data.get('tool_name', '')
wd = '/Users/olgakhaidukova/Desktop/ai-homework'

# --- Определить путь к файлу ---
fp = ''
is_external = False  # файл пришёл извне (Finder, терминал и т.п.)

if tool_name == 'Write':
    fp = data.get('tool_input', {}).get('file_path', '')
elif tool_name == 'Bash':
    cmd = data.get('tool_input', {}).get('command', '')
    # Ловим cp ... <путь в рабочей директории>
    if not re.search(r'\bcp\b', cmd):
        sys.exit(0)
    tokens = cmd.split()
    for token in reversed(tokens):
        clean = token.rstrip('/')
        if clean.startswith(wd + '/') or clean == wd:
            fp = clean
            break
    if not fp:
        sys.exit(0)
elif tool_name == 'FileChanged':
    # Файл изменился извне (Finder, копирование и т.п.)
    fp = (data.get('file_path') or
          data.get('tool_input', {}).get('file_path', '') or
          data.get('path', ''))
    is_external = True
else:
    sys.exit(0)

if not fp:
    sys.exit(0)

# --- Только файлы внутри рабочей директории ---
if not (fp.startswith(wd + '/') or fp == wd):
    sys.exit(0)

basename = os.path.basename(fp)
if not basename:
    sys.exit(0)

# --- Пропускаем служебные файлы ---
SKIP_NAMES = {
    'CLAUDE.md', 'settings.json', 'settings.local.json',
    'MEMORY.md', 'rename-check.py', '.DS_Store', '.gitignore',
    'MEMORY.md',
}
SKIP_EXTS = {'.py', '.sh', '.json', '.lock'}

_, ext = os.path.splitext(basename)
if basename.startswith('.') or basename in SKIP_NAMES or ext.lower() in SKIP_EXTS:
    sys.exit(0)

# --- Проверяем соответствие соглашению ---
# Хорошее имя: содержит хотя бы один дефис или подчёркивание (разделитель компонентов)
# Плохое имя: одно слово без разделителей, пробелы, авто-имена
name_without_ext = os.path.splitext(basename)[0]
has_separator = '_' in name_without_ext or '-' in name_without_ext
has_spaces = ' ' in basename
looks_like_autogen = bool(re.match(r'^(Screenshot|Снимок|Untitled|Копия|Copy|Document|file\d*|новый)', name_without_ext, re.IGNORECASE))

needs_rename = not has_separator or has_spaces or looks_like_autogen

if not needs_rename:
    sys.exit(0)

# --- Сообщаем модели о нарушении ---
msg = (
    f'Файл "{basename}" не соответствует соглашению об именовании. '
    f'Формула: [дата]_[тема]_[тип-файла].[расширение]. '
    f'Примеры: 2026-05_webinar-anons_email.md, client-export_report.csv, fns-guide_doc.md. '
    f'Полный путь: {fp}. Переименуй файл через mv.'
)

if is_external:
    # FileChanged: печатаем в stdout и выходим с кодом 2 — это будит модель
    print(msg)
    sys.exit(2)
else:
    # PostToolUse: инжектируем additionalContext
    result = {
        'hookSpecificOutput': {
            'hookEventName': 'PostToolUse',
            'additionalContext': msg
        }
    }
    print(json.dumps(result))
