# Running Web-VLMAgent

[Back to project overview](../README.md)

## Requirements

- Python 3.10 or newer
- Chromium installed through Playwright
- An API key for the selected model provider

## Environment Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python -m playwright install chromium

Copy-Item .env.example .env
```

Example `.env` configuration:

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

GEMINI_MODEL=gemini-3.5-flash
```

For an OpenAI-compatible API proxy, set `OPENAI_BASE_URL` and `OPENAI_MODEL` to the endpoint and model supported by your provider.

## Run a Single Task

```powershell
python scripts/run_task.py `
  --url "https://csrankings.org/#/fromyear/2020/toyear/2025/index?nlp&us" `
  --task "Find the professor with the most publications at the highest-ranked US university in NLP for 2020-2025." `
  --provider openai `
  --model gpt-4.1-mini `
  --observation_mode vision `
  --max_steps 12
```

Available `--observation_mode` values:

- `vision`: Sends the raw screenshot and SoM screenshot for vision-capable models.
- `text`: Sends only the DOM, page text, and accessibility tree for text-only models.
- `auto`: Selects the mode automatically based on the provider.

## Batch Evaluation

Run a single task file:

```powershell
python scripts/run_batch_eval.py `
  --task_file tasks/csrankings.yaml `
  --repeat 1 `
  --provider openai `
  --model gpt-4.1-mini `
  --observation_mode vision
```

Evaluate 10 models on the eight core tasks (the default task files):

```powershell
python scripts/run_model_suite_parallel.py `
  --suite_tag eval_10models_8coretasks `
  --model_set official_20260622 `
  --max_workers 2 `
  --repeat 1 `
  --headless true
```

If space on the C: drive is limited, save traces to another drive:

```powershell
python scripts/run_model_suite_parallel.py `
  --suite_tag eval_10models_8coretasks `
  --model_set official_20260622 `
  --max_workers 2 `
  --repeat 1 `
  --headless true `
  --trace_dir D:\Web-VLMAgent-traces\model_suite
```

Combine reports from multiple suites:

```powershell
python scripts/collect_model_suite_reports.py `
  --tag eval_10models_8coretasks `
  --output_dir reports\model_suite\combined_eval_10models_8coretasks
```

## Evaluation Task Files

| File | Task Count | Website / Scenario |
|---|---:|---|
| `tasks/csrankings.yaml` | 2 | Core: CSRankings easy / medium |
| `tasks/bilibili.yaml` | 3 | Core: Bilibili easy / medium / hard |
| `tasks/guangzhou_travel.yaml` | 3 | Core: Guangzhou travel planning easy / medium / hard |

The three default files contain eight core tasks in total. The supplementary task is stored separately in `tasks/exploratory/csrankings.yaml` and is excluded from default suites and core completion rates. Its task specification needs review before scored reuse. To inspect it independently, pass that file explicitly to `scripts/run_batch_eval.py`; report those exploratory runs separately.

## Action Space

The model must output structured JSON actions:

```json
{
  "thought": "brief reason",
  "action": "click|type|scroll|press|back|wait|answer|done|ask_user|navigate|search",
  "target_id": 1,
  "text": "string or null",
  "direction": "up|down|null",
  "key": "string or null",
  "confidence": 0.0,
  "checklist_updates": []
}
```

Safety constraints:

- `click` and `type` must reference a valid `target_id`.
- Login, payments, purchases, deletion, orders, reservations, and submission of sensitive information are prohibited.
- Arbitrary model-generated code must never be executed.
- On encountering a CAPTCHA, login wall, or high-risk action, the agent should use `ask_user` or stop.

## Metrics

Batch evaluation reports:

- Task Success Rate
- Average Steps
- Average Time
- Action JSON Validity Rate
- Grounding Error Rate
- Correction / Retry Success Rate
- Failure Type Distribution: perception / planning / execution / verification / website

Success is measured at two levels:

- `harness_success`: The agent loop's own completion judgment, primarily indicating whether it stopped normally and produced an answer.
- `validator_success` / `validated_success`: Post-run validation against reference answers or field-coverage criteria. The three CSRankings tasks use deterministic answer checks. Open-ended tasks such as Bilibili and travel planning receive field/source coverage scores and are flagged with `validator_needs_review=true` for human or LLM-judge review.

Validate a single trace:

```powershell
python scripts/evaluate_trace.py `
  --run_dir traces/task_run/<run_id> `
  --task_file tasks/csrankings.yaml
```

Report outputs:

```text
reports/model_suite/{suite_id}/
  summary.csv
  summary.md
  model_summary.csv
  task_summary.csv
  trace_report.html
```

`reports/` and `traces/` contain generated runtime artifacts and are excluded from Git by default.

## Workbench and Trace Replay

Launch the interactive workbench from the repository root:

```powershell
python webui/server.py --host 127.0.0.1 --port 8601
```

Open <http://127.0.0.1:8601> to create a task and inspect its trace. The Streamlit viewer is also available:

```powershell
streamlit run webui/app.py
```

You can also open the `trace_report.html` produced by batch evaluation directly.

## Development Checks

```powershell
python -m compileall agents browser eval scripts
```

Before committing, ensure the following are excluded:

- `.env`
- `traces/`
- Large generated `reports/`
- Browser caches, virtual environments, and `node_modules/`
