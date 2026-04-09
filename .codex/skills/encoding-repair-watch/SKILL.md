---
name: encoding-repair-watch
description: Diagnose and repair mojibake, BOM, and mixed-encoding issues in this repo, especially when PowerShell output shows corrupted Chinese text or Python files contain broken string literals or docstrings.
---

# Encoding Repair Watch

Use this skill when the user sees:
- corrupted Chinese text in PowerShell or editors
- odd characters like `撠??` or `??`
- Python syntax errors caused by broken string literals
- files that were saved with the wrong encoding

## Scope

This repo often needs careful handling of:
- UTF-8 vs UTF-8 BOM
- PowerShell display encoding
- old files that may have been saved in Big5/CP950-like encodings
- mixed clean text and mojibake in the same file

## Workflow

1. Verify whether the problem is display-only or file-content corruption.
2. Inspect the raw file with a safe read path.
3. Prefer targeted fixes over mass rewrite.
4. Preserve code behavior; only normalize text where needed.
5. Re-run syntax checks after any rewrite.

## Guardrails

- Do not assume every strange character is a file bug; confirm first.
- If a file is already partially corrupted, rewrite only the minimal safe section or the whole file when partial patching is too risky.
- Keep ASCII-only edits unless the repo file already clearly uses Chinese text.
- After a rewrite, verify with `python -m py_compile` or the repo's relevant test command.

## Common Fix Pattern

- If PowerShell output looks corrupted, test alternate encodings before editing.
- If a Python file contains broken quotes or docstrings, rewrite the affected function or file in clean UTF-8.
- If a patch repeatedly lands in the wrong place because of mojibake, rewrite the whole function instead of patching fragments.
