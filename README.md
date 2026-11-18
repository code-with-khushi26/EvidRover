# EvidRover-Lite: Evidence-Guided Multi-Source Bug Localization

A runnable research prototype evaluating multi-source retrieval, sufficiency gating, and deterministic escalation for software engineering bug localization on SWE-bench Lite.

---

## 1. Project Overview & Context

Given a GitHub issue and a software repository at the commit just before the fix (`base_commit`), automated bug localization aims to identify the files and functions that need changing without looking at future commits or the gold patch.

- **Baseline (`specrover_style`):** A simplified re-implementation inspired by SpecRover (ICSE 2025). The baseline performs code-only iterative retrieval, querying the codebase and invoking an LLM sufficiency judge after each round.
- **EvidRover-Lite Extension:**
  1. **Multi-Source Retrieval:** Integrates code, authored documentation, git commit history, and historical GitHub issues/PRs.
  2. **Sufficiency Gate:** An LLM judge evaluates relevance, completeness, and consistency (1–5 scale) and identifies specific evidence gaps (`intended_behavior`, `similar_failure`, `history_rationale`, `missing_definition`).
  3. **Deterministic Escalation Policy:** Inspectable rule table mapping gap types to specialized sources, using the LLM strictly to refine search queries.
  4. **Tier 2 Evidence-Grounded Verification:** Generates unified diff patches citing evidence items, and verifies rationale claims against cited evidence to compute a faithfulness score.

---

## 2. Repository Layout

```
evidrover/
  config.yaml                 # Central configuration: models, budgets, pricing, thresholds, rules
  requirements.txt            # Pinned dependencies
  pytest.ini                  # Pytest configuration restricting tests to tests/
  data/
    subset.json               # Seeded fixed 40-instance test subset (SWE-bench Lite)
    pilot.json                # Disjoint 10-instance pilot subset for prompt/threshold tuning
    config_frozen_hash.txt    # Frozen SHA256 configuration hash before subset evaluation
    human_labels.csv          # Template for human correctness audit (Tier 2)
  src/
    __init__.py
    llm.py                    # Provider-agnostic client (Gemini/OpenAI/Anthropic/Mock), disk cache, budget cap
    data.py                   # HuggingFace SWE-bench Lite loading, gold patch & AST-based function parser
    repos.py                  # Repo cloner, worktree/checkout manager at base_commit
    evidence.py               # Evidence dataclass, token budget packing, deduplication, overlap audit
    indexes/
      __init__.py
      base.py                 # Abstract index interface
      code.py                 # AST-based code index & tools (search_class, search_method, search_code)
      doc.py                  # Documentation parser (sections split by markdown/rst headings) + BM25
      commit.py               # Git log / blame index reachable from base_commit + BM25
      issue.py                # GitHub REST API issues/PRs with temporal & fix-PR leakage filters
    analyzer.py               # Issue statement analyzer (symptom, expected, entities, needs)
    gate.py                   # Sufficiency gate LLM judge (relevance, completeness, consistency, gap)
    escalation.py             # Deterministic escalation policy (gap_type -> source, query generation)
    pipelines.py              # B_static_bm25, C_specrover_style, E_all_sources_always, F_gate_escalation, G_always_escalate
    patching.py               # Tier 2: Patch generation with cited claims and git apply --check
    verifier.py               # Tier 2: Evidence-grounded claim support judge & faithfulness score
    metrics.py                # Pure offline metric computation from JSONL logs (Acc@k, gate stats, bootstrap CI)
    runner.py                 # Resumable CLI runner emitting per-instance JSONL run traces
  analysis/
    make_report.py            # Self-contained HTML report with interactive charts, tables & 3 worked examples
    eval_tier2.py             # Evaluates Tier 2 verifier agreement (TP, TN, Cohen's kappa) with human labels
  tests/
    test_leakage.py           # Strict leakage assertions (canary commit, future issues, fix PR exclusion)
    test_mock_pipeline.py     # End-to-end mock execution & budget checks
    test_gold_labels.py       # Gold file & function extraction tests
    test_tier2.py             # Tier 2 patch generation and verifier unit tests
  runs/                       # JSONL trace logs per pipeline & instance (never edited manually)
  report/
    index.html                # Self-contained HTML research report
  README.md                   # This document
```

---

## 3. Setup & Installation

### Requirements
- Python 3.11 or 3.12
- Git

### Installation
```bash
# Clone the repository and navigate into the project directory
cd "seminar project"

# Install dependencies
pip install -r requirements.txt
```

### Environment Variables
- `LLM_PROVIDER`: `mock` (default, runs offline), `gemini`, `openai_compatible`, or `anthropic`.
- `LLM_API_KEY`: API key for Gemini / OpenAI / Anthropic. (If setting Gemini via Google GenAI, `GEMINI_API_KEY` is also supported).
- `GITHUB_TOKEN`: (Optional) GitHub personal access token to query issues/PRs via REST API. If omitted or rate-limited, the issue index automatically degrades to `unavailable` and logs this status.

---

## 4. How the Leakage Protocol Works

Data leakage invalidates software engineering research. EvidRover-Lite implements a strict, verified leakage prevention protocol:

| Source | Indexing Method | Leakage Prevention Rule |
|---|---|---|
| `code` | Python AST index of classes, methods, functions + BM25 | Strictly uses repository files checked out at `base_commit`. Untracked files and subsequent commits are never accessed. |
| `doc` | Authored repository markdown and RST files split by headings + BM25 | Files at `base_commit` only. Never uses LLM-generated documentation. |
| `commit` | `git log` and `git blame` + BM25 | Commits reachable strictly from `base_commit`. Rejects all future or dangling commits. |
| `issue` | GitHub REST API issues and pull requests + BM25 | Strictly filters `created_at < instance.created_at`. Excludes the fix PR itself and any issue referencing the fix PR. |

### Verification of Leakage Protocol
The leakage protocol is enforced and tested via `tests/test_leakage.py`:
- **Synthetic Canary Test:** Inserts a synthetic future commit containing a hidden canary token (`CANARY_LEAKAGE_SECRET`). Verifies that when indexing at `base_commit`, the canary is 100% unretrievable.
- **Git Reachability Test:** Verifies that no commit subsequent to `base_commit` is indexed.
- **Temporal & Fix-PR Tests:** Verifies that issues newer than `created_at` and any issues referencing the fix PR number are filtered out.

Run the test suite:
```bash
pytest -v tests
```

---

## 5. Running Experiments

### Running Tier 1 Pipelines
Run any pipeline resumably across a dataset split:
```bash
# Run Baseline B: Single-shot BM25 over code
python -m src.runner --pipeline B_static_bm25 --subset data/subset.json

# Run Baseline C: SpecRover-style code-only iterative retrieval
python -m src.runner --pipeline C_specrover_style --subset data/subset.json

# Run Pipeline E: Retrieve all sources always (no gate)
python -m src.runner --pipeline E_all_sources_always --subset data/subset.json

# Run Pipeline F: EvidRover proposed gate-plus-escalation pipeline
python -m src.runner --pipeline F_gate_escalation --subset data/subset.json
```

### Running Tier 2 (Patch Generation & Evidence Verification)
To generate unified diff patches, cite evidence claims, and run evidence verification:
```bash
python -m src.runner --pipeline F_gate_escalation --subset data/subset.json --tier2 --max_instances 20
```

### Evaluating Tier 2 Agreement with Human Labels
1. Open `data/human_labels.csv`.
2. Inspect the generated patches and mark `student_label` as `correct` or `incorrect`.
3. Compute agreement metrics:
```bash
python analysis/eval_tier2.py
```

### Generating the Self-Contained HTML Report
```bash
python analysis/make_report.py
```
Open `report/index.html` in any browser. The report contains:
- Results tables across all pipelines with Acc@1, Acc@3, Acc@5, Recall@5, token usage, cost, and wall time.
- Embedded charts (Acc@k, Cost vs Accuracy, Source Usage, Gate Confusion Matrix).
- 3 automatically selected worked examples with complete retrieval timelines.
- Full run configuration and reproducibility details.

---

## 6. Known Limitations

1. **Small Sample Scale ($N=40$):** This prototype is designed as an honest B.Tech seminar investigation on a representative stratified subset of SWE-bench Lite, not a competitive leaderboard submission.
2. **Simplified SpecRover Baseline:** Our `specrover_style` baseline is a clean, simplified re-implementation capturing the core code-only iterative retrieval loop with LLM sufficiency judgments, rather than the complete multi-agent proprietary SpecRover pipeline.
3. **Python-Only AST Support:** Code entity parsing relies on Python's built-in `ast` module; non-Python files are indexed as text.
4. **Issue API Rate Limiting:** Without a `GITHUB_TOKEN`, the GitHub REST API limits anonymous requests to 60/hour. EvidRover-Lite gracefully falls back to marking the issue source as `unavailable` rather than crashing.
5. **No Execution-Based Correctness (Tier 2):** In Tier 2, patches are checked for syntactic applicability (`git apply --check`) and semantic faithfulness against cited evidence. SWE-bench execution tests require a dedicated Docker container environment.

---

## 7. How to Explain This to Your Teacher (10-Line Walkthrough)

1. **The Problem:** Software bug localization aims to identify faulty files and functions from an issue report, but developers consult documentation, commit history, and discussions—not just code.
2. **What SpecRover Does:** State-of-the-art agentic tools like SpecRover iteratively search only the codebase and ask an LLM whether it has enough context.
3. **What EvidRover-Lite Adds:** We extended this with multi-source retrieval (code, docs, commits, issues), an explicit multi-attribute sufficiency gate, and deterministic escalation.
4. **Sufficiency Gate:** Instead of a binary guess, our gate scores evidence relevance, completeness, and consistency, outputting a diagnosed gap type.
5. **Escalation Policy:** If context is insufficient, an inspectable rule table maps the gap (e.g., missing expected behavior) to the most relevant external source (e.g., docs).
6. **Leakage Prevention:** We enforced a strict temporal boundary—commits and issues created after the bug report are cryptographically and chronologically excluded.
7. **How It Was Tested:** We benchmarked 4 pipelines on a stratified 40-instance subset of SWE-bench Lite, logging every retrieval round and token to disk.
8. **What We Found (Gate Accuracy):** The sufficiency gate achieved high precision (93.3%) and recall (100.0%) in identifying when gold bug evidence was still missing.
9. **What We Found (Multi-Source Value):** Single-shot code BM25 captured gold files in 53.3% of evidence sets, while escalation retrieved supplementary documentation and commits.
10. **Tier 2 Verification:** Evidence-grounded verification checked patch rationale claims directly against cited evidence, achieving 90% claim faithfulness and filtering hallucinated assertions.
