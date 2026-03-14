#!/usr/bin/env python3
"""Scan repository for easyocr usages and write easyocr_calls.log

Writes file path, line number, matched line, and small context.
"""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LOG_PATH = os.path.join(ROOT, 'easyocr_calls.log')

PATTERNS = [
    ('import_easyocr', re.compile(r'^\s*(?:from\s+easyocr\s+import|import\s+easyocr)\b')),
    ('easyocr_reader', re.compile(r'\beasyocr\.Reader\b')),
    ('readtext_call', re.compile(r'\.readtext\s*\(')),
    ('easyocr_dot', re.compile(r'\beasyocr\b')),
]

def scan_file(path):
    results = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        try:
            with open(path, 'r', encoding='latin-1') as f:
                lines = f.readlines()
        except Exception:
            return results

    for i, line in enumerate(lines):
        for name, pat in PATTERNS:
            if pat.search(line):
                start = max(0, i-2)
                end = min(len(lines), i+3)
                context = ''.join(lines[start:end])
                results.append({
                    'lineno': i+1,
                    'type': name,
                    'line': line.rstrip('\n'),
                    'context': context
                })
                break
    return results

def main():
    entries = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # skip virtualenvs, hidden and large folders
        skip_dirs = {'.git', '__pycache__', 'venv', '.venv', 'node_modules', '.idea'}
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith('.')]
        for fn in filenames:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT)
            res = scan_file(path)
            if res:
                entries.append((rel, res))

    with open(LOG_PATH, 'w', encoding='utf-8') as out:
        out.write(f'Scanned root: {ROOT}\n')
        out.write(f'Found {sum(len(r) for _, r in entries)} matches in {len(entries)} files\n\n')
        for rel, res in sorted(entries):
            out.write(f'--- File: {rel} ---\n')
            for r in res:
                out.write(f"[{r['lineno']}] {r['type']}: {r['line']}\n")
                out.write('Context:\n')
                out.write(r['context'])
                out.write('\n')
    print('Wrote', LOG_PATH)

if __name__ == '__main__':
    main()
