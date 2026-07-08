import argparse
from pathlib import Path

from .orchestrator import configure_logger
from .pipeline import PipelineOptions, build_pipeline
from .project_detector import detect_project_type
from .workflow import Workflow
from .interactive_cli import run_interactive_mode


def print_pipeline(pipeline):
    for index, step in enumerate(pipeline.steps, start=1):
        print(f"{index}. [{step.stage.value.upper()}] {step.name}")
        print(f"   command: {step.command}")
        print(f"   retry: {step.retry}\n")

    if pipeline.cleanup_steps:
        print("Cleanup steps:")
        for index, step in enumerate(pipeline.cleanup_steps, start=1):
            print(f"  {index}. {step.name}")
            print(f"     command: {step.command}")
            print(f"     retry: {step.retry}\n")


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--path", default=".", help="Project path to inspect")
    common.add_argument("--retries", type=int, default=None, help="Override retry count for every pipeline step")
    common.add_argument("--include-tests", action="store_true", help="Include test execution in the pipeline")
    common.add_argument("--include-lint", action="store_true", help="Include linting in the pipeline")
    common.add_argument("--cleanup", action="store_true", help="Run cleanup steps after pipeline execution")
    common.add_argument("--extra-command", action="append", default=[], help="Add a custom command to the pipeline")
    common.add_argument("--log-file", default=None, help="Write detailed logs to a file")

    parser = argparse.ArgumentParser(description="CI/CD orchestrator CLI")
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("detect", parents=[common], help="Detect project type")
    subparsers.add_parser("pipeline", parents=[common], help="Generate pipeline for project")
    subparsers.add_parser("run", parents=[common], help="Execute pipeline locally")
    subparsers.add_parser("interactive", help="Launch interactive UI mode")

    args = parser.parse_args(argv)

    # Interactive mode
    if args.command == "interactive" or args.command is None:
        return run_interactive_mode()

    project_path = Path(args.path).resolve()
    logger = configure_logger(args.log_file)

    if args.command == "detect":
        project_type = detect_project_type(project_path)
        print(project_type)
        return 0

    project_type = detect_project_type(project_path)
    options = PipelineOptions(
        include_tests=args.include_tests,
        include_lint=args.include_lint,
        cleanup=args.cleanup,
        extra_commands=args.extra_command,
        retry=args.retries,
    )
    pipeline = build_pipeline(project_type, str(project_path), options)

    if args.command == "pipeline":
        print_pipeline(pipeline)
        return 0

    if args.command == "run":
        workflow = Workflow(name=f"{project_type} workflow", pipeline=pipeline)
        results = workflow.execute(cwd=str(project_path), logger=logger)
        failed = any(not result.success for result in results)
        return 1 if failed else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
