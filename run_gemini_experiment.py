"""Automated runner for the preliminary Gemini 2.5 Flash experiment on 5 instances.

Enforces:
1. Pre-flight API quota check (halts if quota not yet reset).
2. Runs all four pipelines (B, C, E, F) on the exact same 5 instances.
3. Saves traces strictly to runs/gemini_experiment/ without touching runs/.
4. Fail-loud policy: stops cleanly on quota exhaustion without falling back to mock.
5. Generates report/gemini_preliminary_report.html from saved traces.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from analysis.make_report import build_report
from src.data import DatasetManager
from src.llm import BudgetExceededError, LLMClient
from src.pipelines import PipelineRunner
from src.repos import RepoManager
from src.runner import run_instance

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("evidrover.gemini_experiment")


def preflight_quota_check(client: LLMClient) -> bool:
    """Sends a 1-token probe to confirm that Gemini API quota is active."""
    logger.info("Performing pre-flight quota check for Gemini API...")
    try:
        res = client.complete("Ping", system="Reply with 'OK'")
        if res.provider == "gemini" and not res.cached:
            logger.info("Pre-flight quota check PASSED! Gemini API is active and responding.")
            return True
        elif res.provider == "mock":
            logger.error("Client returned mock provider. Ensure LLM_PROVIDER=gemini and valid key in .env.")
            return False
        return True
    except Exception as e:
        logger.error(f"Quota check FAILED: {e}")
        return False


def main() -> None:
    config_path = "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Force live gemini provider
    config["llm"]["provider"] = "gemini"
    config["llm"]["model"] = os.environ.get("LLM_MODEL", "gemini-2.5-flash")

    llm_client = LLMClient(config)
    repo_mgr = RepoManager(config)
    dataset_mgr = DatasetManager(config)
    runner = PipelineRunner(config, llm_client)

    # 1. Preflight check
    if not preflight_quota_check(llm_client):
        logger.warning(
            "\n[QUOTA INSUFFICIENT] Gemini free-tier daily quota has not reset yet.\n"
            "Please check back when Google resets the daily window (see reset time in prior error).\n"
            "Execution aborted safely before touching experiment logs."
        )
        sys.exit(1)

    # 2. Load the 5 instances
    instances_file = Path("data/preliminary_5.json")
    if not instances_file.exists():
        logger.error(f"Subset file {instances_file} not found.")
        sys.exit(1)

    with open(instances_file, "r", encoding="utf-8") as f:
        instance_ids = json.load(f)

    instances = dataset_mgr.get_instances(instance_ids)
    logger.info(f"Loaded {len(instances)} preliminary instances: {instance_ids}")

    # Output directory strictly isolated
    output_dir = Path("runs/gemini_experiment")
    output_dir.mkdir(parents=True, exist_ok=True)

    pipelines = ["B_static_bm25", "C_specrover_style", "E_all_sources_always", "F_gate_escalation"]

    total_completed = 0
    start_time = time.time()

    for pipe_name in pipelines:
        logger.info(f"\n==========================================")
        logger.info(f"Starting Pipeline: {pipe_name}")
        logger.info(f"==========================================")

        for inst in instances:
            try:
                trace = run_instance(runner, repo_mgr, pipe_name, inst, output_dir, force=False)
                if trace:
                    total_completed += 1
            except Exception as e:
                logger.error(
                    f"\n[EXECUTION HALTED] Pipeline {pipe_name} on instance {inst.instance_id} failed: {e}\n"
                    f"Possible rate-limit / quota exhaustion reached.\n"
                    f"Stopping immediately per fail-loud protocol."
                )
                sys.exit(1)

    elapsed = time.time() - start_time
    logger.info(
        f"\nPreliminary experiment complete! Completed {total_completed} pipeline runs across {len(instances)} instances in {elapsed:.1f}s."
    )

    # 3. Generate preliminary report
    from analysis.make_report import build_report
    report_file = "report/gemini_preliminary_report.html"
    logger.info(f"Generating preliminary HTML report at {report_file}...")
    build_report(config_path=config_path, runs_dir=str(output_dir), output_path=report_file)
    logger.info(f"Report generated: {report_file}")


if __name__ == "__main__":
    main()
