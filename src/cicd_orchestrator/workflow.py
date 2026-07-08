import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .orchestrator import configure_logger, execute_steps
from .pipeline import Pipeline
from .database import get_db


@dataclass
class Workflow:
    name: str
    pipeline: Pipeline
    description: Optional[str] = None

    def execute(
        self,
        cwd: str = ".",
        logger: Optional[logging.Logger] = None,
        user_id: str = "anonymous",
        track: bool = True,
    ):
        if logger is None:
            logger = configure_logger()

        db = get_db() if track else None
        run_id: Optional[str] = None

        if db:
            run_id = db.create_run(
                project_path=cwd,
                project_type=self.pipeline.project_type,
                user_id=user_id,
            )

        logger.info("Starting workflow: %s (run_id=%s)", self.name, run_id or "N/A")
        results = execute_steps(self.pipeline.steps, cwd=cwd, logger=logger)

        if self.pipeline.cleanup_steps:
            logger.info("Running cleanup steps")
            cleanup_results = execute_steps(
                self.pipeline.cleanup_steps,
                cwd=cwd,
                logger=logger,
                stop_on_failure=False,
            )
            results.extend(cleanup_results)

        if db and run_id:
            for idx, result in enumerate(results):
                db.add_step_result(
                    run_id=run_id,
                    step_index=idx,
                    step_name=result.step.name,
                    command=result.step.command,
                    stage=result.step.stage.value,
                    success=result.success,
                    attempts=result.attempts,
                    output=result.output or "",
                )
            passed = sum(1 for r in results if r.success)
            failed = len(results) - passed
            db.finish_run(
                run_id=run_id,
                passed=passed,
                failed=failed,
                total=len(results),
                status="success" if failed == 0 else "failed",
            )

        return results, run_id
