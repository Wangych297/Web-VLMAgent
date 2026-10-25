# Evaluation Notes

[Back to project overview](../README.md)

## Evidence and Protocol

The figures below follow the core evaluation scope in the team's [project report](../final_report/Web-VLMAgent_Project_Report.pdf), Sections 4.3-4.7 and Tables 10-13. Model names are the labels recorded in that report. No new model runs were performed for the README update, and raw experiment traces are not distributed with this repository. The report provides aggregate evidence; this repository provides the implementation and task definitions.

## Task Scope

The task collection consists of **eight core evaluation tasks and one supplementary exploratory task**. The core set contains CSRankings easy/medium, all three Bilibili tasks, and all three Guangzhou travel tasks. The supplementary CSRankings cross-area ranking/faculty task is stored in `tasks/exploratory/csrankings.yaml`; its specification requires review, so it is used for task-design and long-horizon diagnostics rather than core completion-rate reporting.

The report has been revised to use the same eight-core-plus-one-exploratory distinction. Historical batches included both scopes; their mixed totals are kept separate from core results. No replacement score is assigned to the exploratory task, and no experiment was rerun for this documentation update.

The repeated baseline, the one-run-per-task vision baseline, and the exploratory cross-model study are separate experiments and are not pooled into a single success rate.

- **Harness completion:** the agent's own run loop stops and produces an answer.
- **Strict validation:** deterministic checks against reference answers for CSRankings.
- **Open-task coverage:** checks for requested fields and source coverage; not a complete semantic or factual correctness assessment. Trace-based human or LLM-judge review is required.

## Repeated Baseline: DeepSeek V4 Pro

Eight tasks, ten runs each: **76/80 harness completions (95.00%)**. These are the counts for the current task scope, derived from Table 11. Aggregate time, mean steps, and failure-type totals from the original nine-task experiment are not reused for this subset.

| Scenario | Difficulty | Completed / runs | Completion rate | Mean steps |
|---|---|---:|---:|---:|
| CSRankings | Easy | 10/10 | 100% | 3.4 |
| CSRankings | Medium | 10/10 | 100% | 8.1 |
| Bilibili | Easy | 10/10 | 100% | 2.2 |
| Bilibili | Medium | 10/10 | 100% | 2.6 |
| Bilibili | Hard | 10/10 | 100% | 26.25 |
| Guangzhou travel | Easy | 10/10 | 100% | 5.1 |
| Guangzhou travel | Medium | 7/10 | 70% | 10.7 |
| Guangzhou travel | Hard | 9/10 | 90% | 34.1 |

Both CSRankings tasks and all three Bilibili tasks completed in 10/10 runs each. Guangzhou attraction comparison and two-day planning completed in 7/10 and 9/10 runs. Open-task completion should be interpreted with the coverage-check limitation above.

## Vision Baseline: GPT-5.5

One run per task in vision mode. The eight tasks in the current scope were all reported complete, with **9.75 steps on average**: (3 + 15 + 2 + 1 + 20 + 4 + 6 + 27) / 8. The source report records no grounding errors for the full baseline. Subset timing is not reported because per-task durations are unavailable in Table 12.

| Task | Steps | Reported validation |
|---|---:|---|
| CSRankings: NLP professor | 3 | Deterministic validator passed |
| CSRankings: ML scholar profile | 15 | Deterministic validator passed |
| Bilibili: public video | 2 | Field coverage 1.0; trace review required |
| Bilibili: AI search comparison | 1 | Field coverage 1.0; trace review required |
| Bilibili: recommendation report | 20 | Field coverage 1.0; trace review required |
| Guangzhou: top attraction | 4 | Field coverage 1.0; trace review required |
| Guangzhou: attraction comparison | 6 | Field coverage 1.0; trace review required |
| Guangzhou: two-day plan | 27 | Field coverage 1.0; trace review required |

This one-run-per-task baseline demonstrates observed completion under that configuration. It does not estimate repeated-run reliability or establish a general 100% answer-accuracy claim.

## Ten-Model Comparison

The original study explored ten configurations: GPT-4o mini, GPT-5 mini, Gemini 2.5 Flash, Claude Haiku 4.5, Qwen3-VL-32B, Doubao Vision Pro, DeepSeek V4 Pro, Qwen3.7-Max, Kimi-K2.6, and GLM-5.2, using vision or text observations according to model capability.

The original report's per-model aggregate table does not provide enough information to recompute every model's results for the current eight-task scope. For this reason, the README describes the breadth and qualitative findings of the study without publishing a subset leaderboard or combining incompatible denominators.

The report distinguishes valid, invalid, and incomplete runs. Invalid samples include API timeouts, upstream content-policy blocks, and context/image token limits. Different observation modes and budgets affect comparability, so this is an exploratory study rather than a controlled model ranking.

## Findings and Limits

1. **Planning and evidence management remain important.** The original error analysis documents repeated navigation, missing subgoals, and weak evidence organization; its mixed-batch failure proportions are not used as statistics for the eight-task core summary.
2. **Text observations can support information tasks.** DOM candidates, accessibility trees, and page text provide useful evidence; this study does not isolate the causal effect of adding vision.
3. **Richer observations have a cost.** Sending raw and marked screenshots together with long DOM/A11y text can exceed model or proxy limits.
4. **Presentation quality is not factual accuracy.** A polished HTML report still needs source checks. Field coverage cannot establish that every recommendation or statement is correct.
5. **Live websites are variable.** Content, geolocation, popups, access restrictions, and service endpoints can change between runs.

## Visual Provenance

- `assets/workbench.webp`: original embedded screenshot from presentation slide 6.
- `assets/travel-report.webp`: full-page capture rendered directly from presentation slide 5, preserving the complete two-part layout.
- `assets/evaluation.png`: English chart using the eight in-scope task counts from report Table 11; 76/80 and 95.00% are recomputed for that scope.
- `assets/architecture.png`: English diagram redrawn from the report's system description and the implementation.

Historical screenshots preserve the original website language and Web-MMAgent branding. The README prose, captions, and newly drawn figures use English and the current Web-VLMAgent name.
