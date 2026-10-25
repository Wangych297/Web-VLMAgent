<div align="center">

# Web-VLMAgent

### See the page. Take action. Keep the evidence.

A multimodal web agent built from scratch for real-browser research, multi-page planning, and report generation.

**3 scenarios** &nbsp; / &nbsp; **8 evaluated tasks** &nbsp; / &nbsp; **80 repeated runs** &nbsp; / &nbsp; **10-model study**

[Overview](#project-overview) · [Architecture](#how-it-works) · [Tasks](#real-website-tasks) · [Showcase](#showcase) · [Results](#evaluation-results) · [Quick Start](#quick-start)

A collaborative project by **Yicheng Wang**, **Shengwei Wang**, and **Lechao Wu**.

</div>

## Project Overview

**Built from scratch by our team.** We independently designed and implemented the core agent architecture, multimodal observation pipeline, browser-action loop, task orchestration, verification, trace replay, and evaluation workbench. The system is not a fork or extension of OpenClaw or another existing open-source agent framework. It uses general-purpose libraries such as Playwright and Pydantic, together with model APIs; ReAct and Set-of-Marks serve as methodological references.

**From a natural-language request to browser actions and a visual report.** Web-VLMAgent reads screenshots and page structure, grounds actions in numbered elements, collects information across pages, and turns its findings into a browsable HTML report.

- **Multimodal grounding:** raw screenshots, Set-of-Marks annotations, DOM candidates, accessibility trees, and page text share one observation pipeline. Vision and text-only models use the same structured action interface.
- **Long-task orchestration:** task planning, checklists, progress checks, and a final verifier support multi-page information gathering and help detect loops or premature completion.
- **Inspectable execution:** every step preserves the observation, prompt, model response, parsed action, execution result, and verification artifacts for replay and error analysis.
- **An evaluation workbench:** YAML tasks, repeated runs, multi-model suites, deterministic validators, open-task coverage checks, and HTML reports connect implementation to measurable experiments.

## How It Works

![English system architecture: observe, plan, act, check, and verify, with per-step traces](docs/assets/architecture.png)

1. **Observe:** capture the page and extract visible candidate elements with numbered marks.
2. **Plan:** combine the task, observation, and action history into a ReAct-style decision.
3. **Act:** validate the structured JSON action and execute it through the browser backend. Clicks and typing reference a current `target_id`.
4. **Check:** update task progress and detect stalled or repetitive behavior.
5. **Verify:** inspect completion evidence, preserve the trace, and generate an answer/report.

<details>
<summary><strong>Explore the implementation</strong></summary>

| Component | Source |
|---|---|
| Browser control and action validation | [`browser/`](browser/) |
| Set-of-Marks annotation | [`perception/`](perception/) |
| Agent, planning, checklists, and verification | [`agents/`](agents/) |
| Model adapters | [`models/`](models/) |
| Evaluation, metrics, and validators | [`eval/`](eval/) |
| Task definitions | [`tasks/`](tasks/) |
| Workbench and replay | [`webui/`](webui/) |

```text
step_000/
  screenshot.png              Raw browser observation
  marked_screenshot.png       Numbered visual grounding
  accessibility_tree.txt      Accessible page structure
  candidates.json             Actionable DOM elements
  prompt.txt                  Model input
  raw_response.txt            Model output
  parsed_action.json          Validated action
  execution_result.json       Browser feedback
  progress_check.json         Progress assessment
  final_verification.json     Completion evidence
```

</details>

## Real-Website Tasks

Eight evaluation tasks cover **academic information retrieval**, **video analysis**, and **travel planning** on live websites.

| Scenario | Task | Difficulty | Goal |
|---|---|---|---|
| **CSRankings** | NLP professor lookup | Easy | Find the professor with the most publications at the top-ranked US university in NLP. |
| **CSRankings** | Scholar profile | Medium | Identify a leading ML scholar and build a profile from multiple sources. |
| **Bilibili** | Public video information | Easy | Retrieve a public video's title, URL, and visible description. |
| **Bilibili** | AI search comparison | Medium | Compare the top three AI search results and analyze their intended audiences. |
| **Bilibili** | Recommendation report | Hard | Browse candidate series, cross-check sources, and produce an HTML recommendation page. |
| **Guangzhou travel** | Attraction lookup | Easy | Retrieve the top listed attraction and its visible details. |
| **Guangzhou travel** | Attraction comparison | Medium | Compare at least three Chimelong-related attractions and recommend one. |
| **Guangzhou travel** | Two-day itinerary | Hard | Research hotels, dining, attractions, transport, budget, and a rain-day alternative. |

These eight core tasks define the scope of the results below. A supplementary [exploratory task](tasks/exploratory/csrankings.yaml) is maintained separately for task-design and long-horizon diagnostics. See the [evaluation notes](docs/EVALUATION.md) for the protocol.

## Showcase

![Recorded workbench showing Set-of-Marks grounding, browser observations, an answer panel, and a step timeline](docs/assets/workbench.webp)

*An actual workbench capture from the project presentation: numbered element grounding, task context, and the step-by-step browser timeline in one view.*

![Complete travel-report showcase captured directly from slide 5, with the itinerary and attraction guide together](docs/assets/travel-report.webp)

*The complete travel-report showcase, captured directly from the presentation PDF: the two-day itinerary and attraction/booking guide are shown together in their original layout.*

*These are original presentation screenshots, not interface mockups. They retain the source-language website content and the earlier Web-MMAgent branding. Report appearance illustrates the output format; factual accuracy requires checking the underlying sources.*

## Evaluation Results

| Experiment | Outcome | Evaluation scope |
|---|---|---|
| **Repeated-run baseline: DeepSeek V4 Pro** | **76/80 completed (95.00%)** | 8 tasks, 10 runs per task; harness-reported completion |
| **Vision baseline: GPT-5.5** | **8/8 completed**; 9.75 steps on average | Same 8-task scope, one run per task; 2 CSRankings answers passed deterministic validation; 6 open tasks passed field-coverage checks |
| **Cross-model study** | **10 model configurations explored** | Vision and text observations; planning, grounding, and service-level failure analysis |

![DeepSeek V4 Pro repeated-run results across eight tasks: 76 of 80 runs completed, or 95 percent](docs/assets/evaluation.png)

**From retrieval to multi-page planning.** The repeated-run baseline completed both CSRankings tasks and all three Bilibili tasks in 10/10 runs each. Guangzhou attraction comparison and two-day travel planning completed in 7/10 and 9/10 runs, respectively, showing where multi-page navigation and evidence collection still need improvement.

**Evaluation scope.** Results cover the eight tasks listed above. Harness completion means the agent stopped with an answer. Deterministic validation is used for CSRankings; open-task field/source coverage still requires trace-based review. The GPT-5.5 baseline has one run per task.

Counts follow the eight-core-task scope documented in the project report, Tables 10-12. The report and task organization have been revised to distinguish core evaluation from supplementary exploration; no experiments were rerun for this update. [Protocol, complete task-level results, and limitations →](docs/EVALUATION.md)

## Quick Start

Requires **Python 3.10+** and an API key for your chosen provider. From PowerShell:

```powershell
git clone https://github.com/Wangych297/Web-VLMAgent.git
cd Web-VLMAgent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Set `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` in `.env` for your OpenAI-compatible provider, or configure the corresponding DeepSeek/Gemini settings.

**Open the workbench:**

```powershell
python webui/server.py --host 127.0.0.1 --port 8601
```

Visit **<http://127.0.0.1:8601>** to start a task and inspect its execution.

**Run a task from the command line:**

```powershell
python scripts/run_task.py `
  --url "https://csrankings.org/#/fromyear/2020/toyear/2025/index?nlp&us" `
  --task "Find the professor with the most publications at the highest-ranked US university in NLP for 2020-2025." `
  --provider openai `
  --model gpt-4.1-mini `
  --observation_mode vision `
  --max_steps 12
```

Use a model supported by your configured provider. Live pages and model endpoints may differ from the recorded experiments.

[Environment configuration, batch evaluation, trace validation, and replay →](docs/USAGE.md)

## Scope and Limitations

Open-task field coverage is a preliminary check rather than a factual-accuracy guarantee. Live websites change; popups, navigation, API timeouts, and context limits affect reproducibility. The multi-model study uses different observation modes, so its results are exploratory rather than a controlled model ranking.

Browser execution validates actions and blocks high-risk operations including login, payment, purchases, deletion, and sensitive submissions. The model does not execute arbitrary generated code. See the [action-space and safety details](docs/USAGE.md#action-space).
