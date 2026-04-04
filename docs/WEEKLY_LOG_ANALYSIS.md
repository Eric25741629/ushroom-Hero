# Weekly Log Analysis

Use `tools/weekly_log_analyzer.py` to generate weekly summaries from:
- `logs/*.log`
- `miner/rl_logs/**/events*.jsonl`

## Run Once

```powershell
conda run -n play1 python tools/weekly_log_analyzer.py --week-start 2026-03-23 --days 7
```

Output files:
- `reports/weekly/weekly_report_YYYYMMDD.json`
- `reports/weekly/weekly_report_YYYYMMDD.md`

## Enable LLM Diagnosis

Set your API key first:

```powershell
$env:OPENAI_API_KEY="YOUR_KEY"
conda run -n play1 python tools/weekly_log_analyzer.py --week-start 2026-03-23 --days 7 --use-llm --llm-model gpt-4.1-mini
```

## Suggested Weekly Schedule (Windows Task Scheduler)

Create a weekly task (for example Monday 07:00) that runs:

```powershell
conda run -n play1 python A:\菇勇者全自動掛機\tools\weekly_log_analyzer.py
```

Without `--week-start`, it automatically analyzes the current week (Monday to Monday).
