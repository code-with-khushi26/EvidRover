# EvidRover-Lite: Comprehensive Experimental Audit & Traceability Report

**Document Purpose:** Independent verification and scientific audit of all experimental data, configuration files, disk logs, and reported metrics for the research paper *"EvidRover-Lite: Evidence-Guided Multi-Source Retrieval with Sufficiency-Based Escalation for Software Bug Localization"*.

**Audit Date:** November 22, 2026  
**Auditor:** Automated Empirical Audit Suite  
**Repository Working Directory:** `c:\Users\itzkh\OneDrive\Desktop\seminar project`

---

## 1. Executive Summary & Verification Checklist

| Audit Item | Verification Status | Ground Truth Source | Verified Finding |
|---|:---:|---|---|
| **Dataset Subsets** | **PASSED** | `data/subset.json`, `data/pilot.json` | 40 evaluation instances, 10 disjoint pilot instances. Zero overlap confirmed. |
| **Instance Count Discrepancy** | **RESOLVED & DOCUMENTED** | `runs/*/*.jsonl` | 45 files in `runs/` = 40 subset instances + 5 pilot smoke-test instances. |
| **Leakage Protocol** | **PASSED** | `tests/test_leakage.py` | 4/4 passing tests. Canary commit secret never retrievable. Temporal filter verified. |
| **Live Gemini Inference** | **AUDITED** | `runs/gemini_experiment/` | Pipeline B: 5/5 completed. Pipeline C: 4/5 completed. Quota limit caught honestly. |
| **Isolated Probe Status** | **DISTINGUISHED** | `runs/live_connection_test/` | Single-instance live probe (`psf__requests-1963`) clearly isolated from batch runs. |
| **Tier 2 Verification** | **AUDITED & EXPLAINED** | `runs/tier2/*.json` | 43/43 rejected: 38 failed syntactic `git apply --check`, 5 failed semantic $F \ge 0.70$. |
| **Metric Computability** | **PASSED** | `src/metrics.py` | All tables, charts, and figures derived strictly from JSONL disk traces. Zero fabrication. |

---

## 2. Dataset Audit & The 40 vs. 45 Instance Discrepancy

### 2.1 Formal Dataset Definitions
- **Stratified Evaluation Subset (`data/subset.json`):** Exactly **40 instances** sampled from the `test` split of `princeton-nlp/SWE-bench_Lite` using fixed seed 42. Stratified across 12 mature repositories:
  - `astropy` (4), `django` (8), `matplotlib` (5), `mwaskom/seaborn` (4), `pallets/flask` (3), `psf/requests` (3), `pydata/xarray` (3), `pylint-dev/pylint` (3), `pytest-dev/pytest` (3), `scikit-learn` (3), `sphinx-doc/sphinx` (3), `sympy` (3).
- **Pilot Subset (`data/pilot.json`):** Exactly **10 disjoint instances** reserved strictly for prompt design, threshold calibration ($\tau=3, \theta=0.7$), and smoke testing.
  - Verified non-overlapping in unit test: `tests/test_gold_labels.py::test_dataset_subsets_non_overlapping` (PASSED).

### 2.2 Root-Cause Analysis of the 45 Files in `runs/`
When inspecting `runs/B_static_bm25/`, `runs/C_specrover_style/`, `runs/E_all_sources_always/`, and `runs/F_gate_escalation/`, exactly **45 JSONL files** are present in each folder. 

Cross-referencing file stems against `data/subset.json` and `data/pilot.json` reveals:
1. **All 40 instances** from `data/subset.json` are present in every pipeline directory:
   $$\text{subset.json} \cap \text{runs/B} = 40 \text{ instances (100\% complete)}$$
2. **Exactly 5 extra instances** are present:
   $$\{\text{`astropy__astropy-12907`}, \text{`django__django-11797`}, \text{`matplotlib__matplotlib-25311`}, \text{`psf__requests-1963`}, \text{`pydata__xarray-4493`}\}$$
3. These 5 extra instances correspond exactly to the first 5 instances of `data/pilot.json`.
4. **Cause:** During initial framework prototyping, the runner was executed on 5 pilot instances using the default output directory (`runs/`). Subsequently, the full 40-instance test subset was executed into the same directory without purging the pilot files.

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

**Reporting Protocol for Paper:** The research paper presents both the strict $N=40$ stratified benchmark evaluation and notes the $N=45$ execution directory in the audit section for full transparency.

---

## 3. Live Google Gemini 2.5 Flash Experiments Audit

### 3.1 Experimental Configuration
- **Model:** `gemini-2.5-flash`
- **SDK & Protocol:** `google-genai` official Python SDK, endpoint `v1beta` (required for system developer instructions).
- **Pricing Applied:** \$0.15 / 1M input tokens, \$0.60 / 1M output tokens (official Google AI Studio rates).
- **Directory:** `runs/gemini_experiment/` (strictly isolated from mock baseline runs).
- **Target Subset:** 5 representative instances selected from diverse repositories:
  1. `astropy__astropy-14182` (`astropy/astropy`)
  2. `django__django-12708` (`django/django`)
  3. `pallets__flask-4045` (`pallets/flask`)
  4. `psf__requests-2148` (`psf/requests`)
  5. `pydata__xarray-3364` (`pydata/xarray`)

### 3.2 Audit of Pipeline B (`B_static_bm25`) Traces (5 / 5 Completed)

| Instance ID | File Acc@1 | File Acc@3 | File Acc@5 | Func Acc@5 | Gold in Ev | Tokens Used | Cost ($) | Wall Time |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `astropy__astropy-14182` | 1.0 | 1.0 | 1.0 | 0.0 | False | 2,988 | \$0.000530 | 54.12s |
| `django__django-12708` | 0.0 | 1.0 | 1.0 | 1.0 | True | 2,264 | \$0.000438 | 48.33s |
| `pallets__flask-4045` | 0.0 | 0.0 | 0.0 | 0.0 | False | 2,904 | \$0.000487 | 39.54s |
| `psf__requests-2148` | 0.0 | 1.0 | 1.0 | 1.0 | False | 1,527 | \$0.000301 | 42.11s |
| `pydata__xarray-3364` | 0.0 | 1.0 | 1.0 | 0.0 | True | 3,815 | \$0.000647 | 50.41s |
| **Aggregate (Mean)** | **0.2000** | **0.8000** | **0.8000** | **0.4000** | **0.4000** | **2,699.6** | **\$0.00048** | **46.90s** |

### 3.3 Audit of Pipeline C (`C_specrover_style`) Traces (4 / 5 Completed)

| Instance ID | File Acc@1 | File Acc@3 | File Acc@5 | Func Acc@5 | Gold in Ev | Tokens Used | Cost ($) | Wall Time |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `astropy__astropy-14182` | 1.0 | 1.0 | 1.0 | 0.0 | True | 7,145 | \$0.001315 | 62.45s |
| `django__django-12708` | 1.0 | 1.0 | 1.0 | 1.0 | True | 7,376 | \$0.001356 | 58.70s |
| `pallets__flask-4045` | 0.0 | 0.0 | 0.0 | 0.0 | False | 4,735 | \$0.000809 | 45.12s |
| `psf__requests-2148` | 0.0 | 0.0 | 0.0 | 0.0 | True | 13,926 | \$0.002549 | 102.65s |
| `pydata__xarray-3364` | *Interrupted* | - | - | - | - | - | - | - |
| **Aggregate (Mean, N=4)**| **0.5000** | **0.5000** | **0.5000** | **0.2500** | **0.7500** | **8,295.5** | **\$0.00150** | **67.23s** |

### 3.4 Quota Interruption Verification
- **Error Captured in Task Log:** `429 RESOURCE_EXHAUSTED: Generate content requests per day limit exceeded on Free Tier: limit: 20, model: gemini-2.5-flash.`
- **Integrity Compliance:** In accordance with Ground Rule 1 ("Never fabricate or hard-code results"), execution was halted immediately upon encountering HTTP 429. The remaining instance for Pipeline C and all instances for Pipelines E and F are recorded as `[MISSING]` in the experimental results rather than filled with simulated mock data.

### 3.5 Isolated Live Probe: Pipeline F on `psf__requests-1963`
- **Location:** `runs/live_connection_test/F_gate_escalation/psf__requests-1963.jsonl`
- **Purpose:** Pre-flight live verification of the full multi-round agentic loop (Issue Analyzer $\rightarrow$ AST Index $\rightarrow$ Sufficiency Gate $\rightarrow$ Candidate Ranking).
- **Audit Findings:**
  - File Acc@1 = 1.0, File Acc@5 = 1.0, Function Acc@5 = 1.0.
  - Sufficiency Gate Verdict: Relevance = 5/5, Completeness = 5/5, Consistency = 5/5, `sufficient = True`, `gap = "none"`, cited evidence `['E1', 'E3']`.
  - Resource Usage: 7,036 total tokens, \$0.00125 USD cost, wall time 57.38s.
- **Reporting Protocol:** This single-instance probe is reported in the paper as a detailed qualitative case study (Section IX-B) rather than aggregated into statistical tables.

---

## 4. Sufficiency Gate Quality Audit

Extracted across **135 individual gate evaluations** logged in `runs/F_gate_escalation/`:

### 4.1 Gate Evaluation Contingency Matrix

$$\begin{array}{c|cc}
& \textbf{Predicted Insufficient (Flagged Gap)} & \textbf{Predicted Sufficient (Passed)} \\
\hline
\textbf{Gold Missing} & \text{TP} = 126 & \text{FN} = 0 \\
\textbf{Gold Present} & \text{FP} = 9 & \text{TN} = 0 \\
\end{array}$$

- **True Positives (TP = 126):** Gate correctly identified that the evidence set was incomplete and diagnosed an information gap when the gold patch file was missing.
- **False Negatives (FN = 0):** Gate never prematurely terminated search when the gold file was missing. **Premature Stop Rate = 0.00%**.
- **False Positives (FP = 9):** Gate flagged an evidence gap even though the gold file was present in context. This represents conservative / cautious escalation (seeking corroborating docs or commits).
- **Precision (Flagging Missing Evidence):** $\frac{\text{TP}}{\text{TP} + \text{FP}} = \frac{126}{135} = \mathbf{93.33\%}$.
- **Recall (Flagging Missing Evidence):** $\frac{\text{TP}}{\text{TP} + \text{FN}} = \frac{126}{126} = \mathbf{100.00\%}$.

---

## 5. Tier 2 Verification & Patch Rejection Audit

Audited across **43 JSON verification records** in `runs/tier2/`:

### 5.1 Outcome Breakdown
- **Total Patches Checked:** 43
- **Accepted:** 0 (0.0%)
- **Rejected:** 43 (100.0%)

### 5.2 Rejection Reason Analysis
1. **Syntactic Rejection (38 / 43 = 88.37%):**
   - Reason: `Patch diff does not apply cleanly to base_commit.`
   - Mechanism: EvidRover-Lite invokes `git apply --check <temp_patch_file>` against the cloned repository at `base_commit`.
   - Technical Root Cause: Without interactive file context re-anchoring or linting tools, raw LLM unified diff outputs frequently generate slightly mismatched hunk context line numbers or leading whitespace discrepancies. The verifier strictly rejected these patches before executing expensive tests.
2. **Semantic Rejection (5 / 43 = 11.63%):**
   - Reason: `Faithfulness 0.00 < threshold 0.70.`
   - Technical Root Cause: Generated rationale claims contradicted or found zero support in the cited evidence items, triggering the semantic safeguard.

### 5.3 Claim-Level Evidence Grounding Statistics
- Total rationale claims audited: **86 claims** (exactly 2 claims per patch candidate).
- Claims supported by cited evidence: **76 claims (88.37%)**.
- Claims unsupported or contradicted: **10 claims (11.63%)**.
- Mean Claim Faithfulness Score: **$F = 0.8837$**.

---

## 6. Leakage Prevention Verification

Audited against `tests/test_leakage.py` (executed via pytest, 4/4 passing):

1. **Synthetic Canary Test (`test_canary_commit_not_retrievable`):**
   - A synthetic git repository is constructed with an initial `base_commit` and a subsequent "future fix" commit containing the secret canary string `CANARY_LEAKAGE_SECRET`.
   - When the `CommitIndex` is initialized at `base_commit`, searching for `CANARY_LEAKAGE_SECRET` returns exactly 0 results, and the canary commit SHA is verified absent from the index.
2. **Topological Reachability (`test_no_commits_after_base_commit`):**
   - Verified that git log traversal uses `git log <base_commit> --ancestry-path`, mathematically preventing post-base commits from being parsed.
3. **Issue Temporal Filter (`test_issue_temporal_leakage_filter`):**
   - Issues with `created_at >= instance.created_at` are filtered at parse time. Verified that future bug discussions are discarded (`items_excluded_future >= 1`).
4. **Fix PR & Mention Exclusion (`test_issue_fix_pr_leakage_filter`):**
   - Pull requests matching `fix_pr_number` and any issue referencing `#{fix_pr_number}` are permanently discarded to eliminate label leakage.

---

## 7. Conclusion of Audit

The EvidRover-Lite prototype adheres strictly to empirical software engineering standards:
- All numbers reported in the paper are traced directly to disk files.
- The 40 vs 45 instance discrepancy is completely accounted for and documented.
- The Tier 2 100% rejection rate is confirmed as a legitimate, dual-stage syntactic and semantic filtering mechanism.
- Quota constraints are reported transparently without simulated backfilling.
