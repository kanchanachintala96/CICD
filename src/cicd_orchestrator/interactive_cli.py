"""
Interactive CLI for CI/CD Orchestrator with user-friendly interface
"""
import sys
from pathlib import Path

from .orchestrator import configure_logger
from .pipeline import PipelineOptions, build_pipeline
from .project_detector import detect_project_type
from .workflow import Workflow
from .ui import (
    display_welcome,
    display_menu,
    get_repo_input,
    get_pipeline_options,
    get_ado_credentials,
    display_project_detection,
    display_pipeline_summary,
    display_execution_results,
    confirm_action,
    show_error_message,
    show_success_message,
    print_info,
    print_error,
    Colors,
)


def run_interactive_mode():
    """Run the orchestrator in interactive mode"""
    display_welcome()

    while True:
        try:
            choice = display_menu()

            if choice == '5':
                print_info("Thank you for using CI/CD Orchestrator. Goodbye!")
                return 0

            # Get repository input
            try:
                repo_path = get_repo_input()
            except KeyboardInterrupt:
                print_error("Operation cancelled by user.")
                continue

            # Detect project type
            try:
                project_type = detect_project_type(Path(repo_path))
                display_project_detection(project_type)
            except Exception as e:
                show_error_message(f"Failed to detect project type: {str(e)}")
                continue

            if choice == '2':
                # Just show detection
                continue

            # Get pipeline options
            try:
                options_dict = get_pipeline_options()
                options = PipelineOptions(
                    include_tests=options_dict['include_tests'],
                    include_lint=options_dict['include_lint'],
                    cleanup=options_dict['cleanup'],
                    extra_commands=options_dict['extra_commands'],
                    retry=options_dict['retries'],
                )
            except KeyboardInterrupt:
                print_error("Operation cancelled by user.")
                continue

            # Build pipeline
            try:
                pipeline = build_pipeline(project_type, repo_path, options)
                display_pipeline_summary(
                    project_type,
                    pipeline.steps,
                    options_dict
                )
            except Exception as e:
                show_error_message(f"Failed to build pipeline: {str(e)}")
                continue

            if choice == '3':
                # Just show pipeline without running
                continue

            # ── Deploy to Azure DevOps ────────────────────────────────────────
            if choice == '4':
                try:
                    import os
                    from .azure_devops import AzureDevOpsClient
                    from .pipeline_exporter import export_azure_devops_basic

                    # Use pre-configured credentials from environment
                    env_org     = os.environ.get("AZURE_DEVOPS_URL", "").strip()
                    env_project = os.environ.get("AZURE_DEVOPS_PROJECT", "").strip()
                    env_pat     = os.environ.get("AZURE_DEVOPS_PAT", "").strip()

                    if env_org and env_project and env_pat:
                        print_info("Using pre-configured Azure DevOps credentials:")
                        print_info(f"  Org:     {env_org}")
                        print_info(f"  Project: {env_project}")
                        print_info(f"  PAT:     {'*' * 8}{env_pat[-4:]}")
                        ado_config = {
                            "org_url": env_org,
                            "project": env_project,
                            "pat": env_pat,
                        }
                        # Only ask for repo-specific details
                        ado_config["repo_name"] = input(
                            f"{Colors.BOLD}Repository Name: {Colors.ENDC}"
                        ).strip()
                        branch_in = input(
                            f"{Colors.BOLD}Branch [main]: {Colors.ENDC}"
                        ).strip()
                        ado_config["branch"] = branch_in or "main"
                        name_in = input(
                            f"{Colors.BOLD}Pipeline Name [CI Pipeline]: {Colors.ENDC}"
                        ).strip()
                        ado_config["pipeline_name"] = name_in or "CI Pipeline"
                    else:
                        ado_config = get_ado_credentials()

                    missing = [k for k in ('org_url', 'project', 'pat', 'repo_name')
                               if not ado_config.get(k)]
                    if missing:
                        print_error(f"Required fields missing: {', '.join(missing)}")
                        continue

                    print_info("Connecting to Azure DevOps...")
                    client = AzureDevOpsClient(
                        ado_config['org_url'], ado_config['project'], ado_config['pat']
                    )
                    client.validate_connection()
                    print_info("Connected! Generating azure-pipelines.yml...")

                    yaml_content = export_azure_devops_basic(
                        pipeline,
                        pipeline_name=ado_config['pipeline_name'],
                        branch=ado_config['branch'],
                    )

                    print_info(f"Looking up repository '{ado_config['repo_name']}'...")
                    repo = client.get_repository(ado_config['repo_name'])
                    repo_id = repo['id']

                    print_info("Pushing project source code + azure-pipelines.yml ...")
                    _, file_count, skipped = client.push_directory(
                        repo_id=repo_id,
                        local_path=repo_path,
                        branch=ado_config['branch'],
                        commit_message=(
                            f"Push {project_type} project + CI pipeline"
                            " [CI/CD Orchestrator Agent]"
                        ),
                        extra_files={"/azure-pipelines.yml": yaml_content},
                    )
                    print_info(f"  ✓ {file_count} file(s) committed"
                               + (f" ({len(skipped)} skipped)" if skipped else ""))

                    print_info("Creating pipeline definition...")
                    pipeline_def = client.create_pipeline(
                        name=ado_config['pipeline_name'],
                        repo_id=repo_id,
                        repo_name=ado_config['repo_name'],
                        yaml_path='/azure-pipelines.yml',
                        branch=ado_config['branch'],
                    )
                    pipeline_id = pipeline_def['id']

                    print_info("Triggering pipeline run...")
                    run = client.run_pipeline(pipeline_id, branch=ado_config['branch'])
                    run_id = run['id']

                    show_success_message("Pipeline created and triggered in Azure DevOps!")
                    print_info(f"Pipeline: {client.pipeline_web_url(pipeline_id)}")
                    print_info(f"Run:      {client.run_web_url(run_id)}")

                except KeyboardInterrupt:
                    print_error("Operation cancelled by user.")
                except Exception as e:
                    show_error_message(f"Azure DevOps deployment failed: {str(e)}")

                if not confirm_action("\nRun another pipeline?"):
                    return 0
                continue

            # ── Run locally (choice == '1') ───────────────────────────────────

            # Confirm before execution
            if not confirm_action(f"\n{Colors.BOLD}Ready to execute pipeline?{Colors.ENDC}"):
                print_info("Pipeline execution cancelled.")
                continue

            # Execute pipeline
            try:
                logger = configure_logger()
                workflow = Workflow(name=f"{project_type} workflow", pipeline=pipeline)
                results, run_id = workflow.execute(cwd=repo_path, logger=logger)
                display_execution_results(results)

                failed = any(not result.success for result in results)
                if failed:
                    print_error("Pipeline execution completed with failures.")
                    return_code = 1
                else:
                    show_success_message("Pipeline executed successfully!")
                    return_code = 0

                # Ask if user wants to run another pipeline
                if not confirm_action("\n" + Colors.BOLD + "Run another pipeline?" + Colors.ENDC):
                    return return_code

            except Exception as e:
                show_error_message(f"Error during pipeline execution: {str(e)}")
                continue

        except KeyboardInterrupt:
            print_error("\n\nOperation cancelled by user.")
            return 1
        except Exception as e:
            show_error_message(f"An unexpected error occurred: {str(e)}")
            continue


def main_interactive(argv=None):
    """Entry point for interactive CLI"""
    # If arguments are provided, launch interactive mode anyway
    try:
        return run_interactive_mode()
    except KeyboardInterrupt:
        print_error("\n\nInterrupted by user.")
        return 1
    except Exception as e:
        print_error(f"Fatal error: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main_interactive())
