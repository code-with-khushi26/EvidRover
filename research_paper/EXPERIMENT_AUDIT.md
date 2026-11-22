# EvidRover-Lite: Comprehensive Experimental Audit & Traceability Report (Revised)

**Document Purpose:** Independent scientific audit and data-traceability report for *"EvidRover-Lite: Evidence-Guided Multi-Source Retrieval with Sufficiency-Based Escalation for Software Bug Localization"*.  
**Revision Date:** November 22, 2026  
**Auditor:** Automated Empirical Audit Suite  
**Working Directory:** `c:\Users\itzkh\OneDrive\Desktop\seminar project`

---

## 1. Executive Summary & Verification Matrix

| Audit Item | Verification Status | Ground Truth Source | Verified Finding & Resolution |
|---|:---:|---|---|
| **Dataset Subsets** | **VERIFIED** | `data/subset.json`, `data/pilot.json`, `data/preliminary_5.json` | 40 evaluation instances, 10 disjoint pilot instances (0 overlap). 5-instance subset selected from the 40 for live testing. |
| **Instance Count Discrepancy** | **VERIFIED & RESOLVED** | `runs/*/*.jsonl` | 45 files in baseline directories = 40 subset instances + 5 pilot smoke-test instances run prior to the subset batch. |
| **Execution Modes (Mock vs. Live)** | **VERIFIED & SEPARATED** | `runs/` vs. `runs/gemini_experiment/` | 224 mock traces in `runs/` (offline deterministic stubs); exactly 10 real Gemini 2.5 Flash traces (9 in `runs/gemini_experiment/`, 1 in `runs/live_connection_test/`). |
| **Pipeline B vs. C Performance** | **VERIFIED** | `runs/gemini_experiment/` | Pipeline B achieved higher Acc@5 (80.0% vs. 50.0%) and Func Acc@5 (40.0% vs. 25.0%). Pipeline C doubled top-1 precision (Acc@1: 20.0% $\to$ 50.0%) and increased gold evidence capture (40.0% $\to$ 75.0%), but did not improve every metric. |
| **Pipelines E & F Status** | **VERIFIED** | `runs/gemini_experiment/` | Live runs halted by Google AI Studio Free-Tier daily limit (20 req/day). Marked `[MISSING] / Quota-Exhausted`. Single probe on `psf__requests-1963` treated as an isolated case study. |
| **Cost Comparison Validity** | **QUALIFIED** | SpecRover [2] vs. `config.yaml` | Comparison against SpecRover's \$0.65/issue is qualified: compares a flash-tier retrieval/ranking model against a full GPT-4 multi-agent repair pipeline. |
| **Sufficiency Gate Confusion Matrix** | **VERIFIED & EXPLAINED** | `runs/F_gate_escalation/` | 135 sequential decisions (45 instances $\times$ 3 rounds): TP=126, FP=9, FN=0, TN=0. Evaluated strictly post-hoc; gate has no gold access during runtime. TN=0 due to conservative mock escalation. |
| **Tier 2 Verification** | **VERIFIED & EXPLAINED** | `runs/tier2/*.json` | 43 candidate patches: 38 failed syntactic `git apply --check` (dummy mock diffs); 5 failed semantic threshold ($F < 0.70$); 76/86 claims supported ($F=0.8837$). Claim faithfulness decoupled from patch/functional correctness. |
| **Leakage Protocol Description** | **CORRECTED** | `tests/test_leakage.py` | Exaggerated claims of "provable/cryptographic" guarantees replaced with accurate descriptions of tested controls (topological traversal, temporal filters, canary tests). |

---

## 2. Dataset Subsets & The 40 vs. 45 Instance Discrepancy

### 2.1 Formal Dataset Definitions
- **Stratified Evaluation Subset (`data/subset.json`):** Exactly **40 instances** sampled from the `test` split of `princeton-nlp/SWE-bench_Lite` using fixed seed 42. Stratified across 12 mature repositories:
  - `astropy` (4), `django` (4), `matplotlib` (4), `mwaskom/seaborn` (4), `pallets/flask` (3), `psf/requests` (3), `pydata/xarray` (3), `pylint-dev/pylint` (3), `pytest-dev/pytest` (3), `scikit-learn` (3), `sphinx-doc/sphinx` (3), `sympy` (3).
- **Pilot Subset (`data/pilot.json`):** Exactly **10 disjoint instances** reserved strictly for prompt design, threshold calibration ($\tau=3, \theta=0.7$), and smoke testing:
  - `astropy-12907`, `django-11797`, `matplotlib-25311`, `requests-1963`, `xarray-4493`, `pylint-5859`, `pytest-7373`, `scikit-learn-14087`, `sphinx-8713`, `sympy-22005`.
  - Verified non-overlapping in unit test: `tests/test_gold_labels.py::test_dataset_subsets_non_overlapping` (PASSED).
- **Live Preliminary Subset (`data/preliminary_5.json`):** Exactly **5 instances** drawn directly from the 40-instance evaluation subset across diverse repositories for live API testing under free-tier quota limits:
  - `astropy__astropy-14182`, `django__django-12708`, `pallets__flask-4045`, `psf__requests-2148`, `pydata__xarray-3364`.

### 2.2 Root-Cause Analysis of the 45 Files in `runs/`
When inspecting `runs/B_static_bm25/`, `C_specrover_style/`, `E_all_sources_always/`, and `F_gate_escalation/`, exactly **45 JSONL files** are present in each folder:
1. **All 40 instances** from `data/subset.json` are present in every baseline directory:
   $$\text{subset.json} \cap \text{runs/B} = 40 \text{ instances (100\% complete)}$$
2. **Exactly 5 additional instances** are present:
   $$\{\text{`astropy__astropy-12907`}, \text{`django__django-11797`}, \text{`matplotlib__matplotlib-25311`}, \text{`psf__requests-1963`}, \text{`pydata__xarray-4493`}\}$$
3. These 5 instances correspond exactly to the first 5 instances of `data/pilot.json`.
4. **Cause:** During initial prototyping, the runner was executed on 5 pilot instances using the default output directory (`runs/`). Subsequently, the full 40-instance test subset was executed into the same directory without purging the pilot files.

### 2.3 Empirical Comparison: Strict 40 Subset vs. Full 45 Execution Set

| Pipeline | Metric | Strict Test Subset ($N=40$) | Full Execution Set ($N=45$) | Delta |
|---|---|:---:|:---:|:---:|
| **B (Static BM25)** | Gold In Evidence Rate | **55.00%** (22 / 40) | **53.33%** (24 / 45) | -1.67% |
| | Mean Tokens / Inst | 2,350.9 | 2,288.9 | -62.0 |
| | Total Dollar Cost | \$0.0146 | \$0.0162 | +\$0.0016 |
| **C (SpecRover-Style)**| Gold In Evidence Rate | **7.50%** (3 / 40) | **6.67%** (3 / 45) | -0.83% |
| | Mean Tokens / Inst | 3,833.9 | 3,710.6 | -123.3 |
| | Total Dollar Cost | \$0.0249 | \$0.0275 | +\$0.0026 |
| **E (All Sources)** | Gold In Evidence Rate | **7.50%** (3 / 40) | **6.67%** (3 / 45) | -0.83% |
| | Mean Tokens / Inst | 2,830.8 | 2,774.6 | -56.2 |
| | Total Dollar Cost | \$0.0175 | \$0.0194 | +\$0.0019 |
| **F (Gate Escalation)** | Gold In Evidence Rate | **7.50%** (3 / 40) | **6.67%** (3 / 45) | -0.83% |
| | Mean Tokens / Inst | 3,292.5 | 3,505.7 | +213.2 |
| | Total Dollar Cost | \$0.0223 | \$0.0254 | +\$0.0031 |

---

## 3. Separation of Mock vs. Real Gemini 2.5 Flash Experiments

### 3.1 Trace Provenance Summary
Across all 234 trace files in `runs/`:
- **Real Gemini Inference:** Exactly **10 traces** generated via live calls to Google's `gemini-2.5-flash` endpoint:
  - 5 traces in `runs/gemini_experiment/B_static_bm25/`
  - 4 traces in `runs/gemini_experiment/C_specrover_style/`
  - 1 trace in `runs/live_connection_test/F_gate_escalation/` (`psf__requests-1963`)
- **Mock Mode (Offline):** Exactly **224 traces** generated via deterministic mock stubs:
  - 45 traces in `runs/B_static_bm25/`
  - 45 traces in `runs/C_specrover_style/`
  - 45 traces in `runs/E_all_sources_always/`
  - 45 traces in `runs/F_gate_escalation/`
  - 1 trace in `runs/connection_test/F_gate_escalation/` (`psf__requests-1963`)
  - 43 traces in `runs/tier2/`

### 3.2 Audit of Live Gemini 2.5 Flash Runs (`runs/gemini_experiment/`)

#### Pipeline B (`B_static_bm25`) — 5 / 5 Completed

| Instance ID | Repository | File Acc@1 | File Acc@3 | File Acc@5 | Func Acc@5 | Gold in Ev | Tokens Used | Cost ($) | Wall Time |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `astropy__astropy-14182` | `astropy/astropy` | 1.0 | 1.0 | 1.0 | 0.0 | False | 2,988 | \$0.000530 | 54.12s |
| `django__django-12708` | `django/django` | 0.0 | 1.0 | 1.0 | 1.0 | True | 2,264 | \$0.000438 | 48.33s |
| `pallets__flask-4045` | `pallets/flask` | 0.0 | 0.0 | 0.0 | 0.0 | False | 2,904 | \$0.000487 | 39.54s |
| `psf__requests-2148` | `psf/requests` | 0.0 | 1.0 | 1.0 | 1.0 | False | 1,527 | \$0.000301 | 42.11s |
| `pydata__xarray-3364` | `pydata/xarray` | 0.0 | 1.0 | 1.0 | 0.0 | True | 3,815 | \$0.000647 | 50.41s |
| **Aggregate (Mean)** | — | **0.2000** | **0.8000** | **0.8000** | **0.4000** | **0.4000** | **2,699.6** | **\$0.00048** | **46.90s** |

#### Pipeline C (`C_specrover_style`) — 4 / 5 Completed

| Instance ID | Repository | File Acc@1 | File Acc@3 | File Acc@5 | Func Acc@5 | Gold in Ev | Tokens Used | Cost ($) | Wall Time |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `astropy__astropy-14182` | `astropy/astropy` | 1.0 | 1.0 | 1.0 | 0.0 | True | 7,145 | \$0.001315 | 62.45s |
| `django__django-12708` | `django/django` | 1.0 | 1.0 | 1.0 | 1.0 | True | 7,376 | \$0.001356 | 58.70s |
| `pallets__flask-4045` | `pallets/flask` | 0.0 | 0.0 | 0.0 | 0.0 | False | 4,735 | \$0.000809 | 45.12s |
| `psf__requests-2148` | `psf/requests` | 0.0 | 0.0 | 0.0 | 0.0 | True | 13,926 | \$0.002549 | 102.65s |
| `pydata__xarray-3364` | `pydata/xarray` | *Quota Exceeded* | — | — | — | — | — | — | — |
| **Aggregate (Mean, N=4)** | — | **0.5000** | **0.5000** | **0.5000** | **0.2500** | **0.7500** | **8,295.5** | **\$0.00150** | **67.23s** |

### 3.3 Comparative Analysis: Pipeline B vs. Pipeline C
- **Where Pipeline C Excels:**
  - **Top-1 Precision:** Pipeline C achieved **50.0% Acc@1** compared to **20.0%** for Pipeline B.
  - **Gold Evidence Capture:** Pipeline C captured the gold file in **75.0%** of instances compared to **40.0%** for Pipeline B.
- **Where Pipeline B Excels:**
  - **Top-5 Accuracy:** Pipeline B achieved **80.0% Acc@5** compared to **50.0%** for Pipeline C on this sample.
  - **Function-Level Accuracy:** Pipeline B achieved **40.0% Func Acc@5** compared to **25.0%** for Pipeline C.
  - **Resource Footprint:** Pipeline B consumed $3.07\times$ fewer tokens (2,699.6 vs. 8,295.5) and lower cost (\$0.00048 vs. \$0.00150).
- **Scientific Takeaway:** Iterative agentic search successfully concentrates context onto the singular root-cause entity (doubling Acc@1), but does not universally dominate broader top-5 coverage metrics on small samples.

### 3.4 Quota Limit & Isolated Probe Status
- Google AI Studio Free-Tier enforces a strict daily limit of 20 requests per day for `gemini-2.5-flash`. In an agentic workflow consuming 3 to 6 LLM interactions per issue, quota is reached in 4 to 5 instances.
- Pipelines E and F were halted by this quota ceiling and are marked `[MISSING] / Quota-Exhausted`.
- **Live Probe on `psf__requests-1963`:** Executed under `runs/live_connection_test/F_gate_escalation/psf__requests-1963.jsonl`. Pinpointed `requests/sessions.py` (Rank 1) and `Session.send` (Rank 1) with Acc@1=1.0, Acc@5=1.0, Func Acc@5=1.0, consuming 7,036 tokens (\$0.00125) with a 5/5/5 Sufficiency Gate score. This run is reported strictly as an illustrative single-instance case study.

---

## 4. Sufficiency Gate Confusion Matrix Audit

Extracted across **135 sequential gate evaluations** logged in `runs/F_gate_escalation/` (45 instances $\times$ 3 rounds = 135 decisions):

$$\begin{array}{c|cc}
& \textbf{Predicted Insufficient (Flagged Gap)} & \textbf{Predicted Sufficient (Passed)} \\
\hline
\textbf{Gold Missing} & \text{TP} = 126 & \text{FN} = 0 \\
\textbf{Gold Present} & \text{FP} = 9 & \text{TN} = 0 \\
\end{array}$$

### 4.1 Methodology & Definitions
- **Operational Definition of "Gold Missing":** A round is designated as "Gold Missing" if no file from the ground-truth resolution patch $\mathcal{F}^*$ is present in the evidence set $E_r$ accumulated up to round $r$.
- **Offline Evaluation Only:** Ground-truth gold labels are used **exclusively for post-hoc offline evaluation**. The sufficiency gate never has access to gold files or gold functions during execution.
- **Decision Counting:** Evaluated on every individual round decision across all 45 instances (3 rounds per instance = 135 decisions).
- **Metric Calculations:**
  - **Precision (Flagging Incomplete Context):** $\frac{\text{TP}}{\text{TP} + \text{FP}} = \frac{126}{135} = \mathbf{93.33\%}$.
  - **Recall (Flagging Incomplete Context):** $\frac{\text{TP}}{\text{TP} + \text{FN}} = \frac{126}{126} = \mathbf{100.00\%}$.
  - **Premature Stop Rate:** $\frac{\text{FN}}{\text{TP} + \text{FN}} = \frac{0}{126} = \mathbf{0.00\%}$.
- **Why $\text{TN} = 0$ in this Baseline:** In mock execution, the mock gate returned default non-passed scores (relevance=3, completeness=3, consistency=3, sufficient=False). Because the gate never declared sufficiency during mock baseline runs, $\text{TN}$ is zero. The 9 false positives represent cases where the gold file was retrieved early, but the gate conservatively continued searching for additional corroborating context.

---

## 5. Tier 2 Verification Audit

Audited across **43 JSON verification records** in `runs/tier2/`:

### 5.1 Outcome Breakdown
- **Total Patches Evaluated:** 43
- **Accepted:** 0 (0.0%)
- **Rejected:** 43 (100.0%)
  - **Syntactic Rejections (38 / 43 = 88.37%):** `Patch diff does not apply cleanly to base_commit.` Mock patch generation emitted a canned diff targeting `core/handler.py`, which does not exist in the evaluated benchmark repositories.
  - **Semantic Rejections (5 / 43 = 11.63%):** `Faithfulness 0.00 < threshold 0.70.` Occurred on the 5 `matplotlib` instances where the evidence set lacked matching IDs `[E1, E2]`, causing all claims to be marked `Unsupported` ($0/2 = 0.0 < 0.70$).

### 5.2 Claim-Level Faithfulness vs. Functional Correctness
- Total claims audited: **86 claims** (2 per candidate patch).
- Supported claims: **76 claims (88.37%)**.
- Unsupported claims: **10 claims (11.63%)**.
- Mean Claim Faithfulness: **$F = 0.8837$**.
- **Crucial Distinction:**
  - **Claim Faithfulness ($F$):** Measures whether the technical assertions made in the patch rationale are substantiated by the text of the cited evidence items.
  - **Patch Syntactic Validity:** Measures whether the unified diff can be applied cleanly via `git apply --check`.
  - **Functional Correctness:** Measures whether the patch resolves the issue and passes repository test suites without regressions.
  - High claim faithfulness does **not** imply patch correctness or functional correctness.

---

## 6. Qualification of Cost Comparison vs. SpecRover

- SpecRover (ICSE 2025) reported an average cost of **\$0.65 per issue** using OpenAI GPT-4 on SWE-bench Lite [2].
- EvidRover-Lite preliminary runs using Google Gemini 2.5 Flash incurred an average cost of **\$0.00048** (Pipeline B) and **\$0.00150** (Pipeline C).
- **Qualification:** This is **not a controlled head-to-head comparison on identical workloads**:
  1. *Model Class:* SpecRover deployed frontier proprietary GPT-4, whereas EvidRover-Lite deployed a high-speed flash-tier model (`gemini-2.5-flash`).
  2. *Pipeline Scope:* SpecRover's \$0.65 included reproducer test generation, full iterative AST retrieval, patch generation, patch review, and test selection loops. EvidRover-Lite's preliminary runs measured the retrieval and localization stages.
  3. *Conclusion:* Flash-tier models offer substantial economic efficiency ($<\$0.002$ per issue) for repository search and ranking, but end-to-end multi-agent repair costs will vary depending on pipeline scope.

---

## 7. Leakage Prevention Protocol Audit

- The protocol enforces tested software engineering controls:
  1. *Working-Tree Isolation:* Repository checked out strictly at $c_{\text{base}}$.
  2. *Topological Reachability:* Commit index traversal enforced via `git log <base_commit> --ancestry-path`.
  3. *Temporal Filtering:* Historical GitHub issues/PRs filtered such that $t_{\text{created}} < t_{\text{issue}}$.
  4. *Fix PR Exclusion:* Explicitly excludes the fix PR number and any issue referencing it.
- Verified via 4 passing unit tests in `tests/test_leakage.py`, including synthetic canary commit tests confirming 0% retrieval of post-base commits.
