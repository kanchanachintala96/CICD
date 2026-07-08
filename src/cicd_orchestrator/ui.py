import os
import sys
from pathlib import Path
from typing import Optional, Tuple
import textwrap


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    @staticmethod
    def disable():
        Colors.HEADER = ''
        Colors.BLUE = ''
        Colors.CYAN = ''
        Colors.GREEN = ''
        Colors.YELLOW = ''
        Colors.RED = ''
        Colors.ENDC = ''
        Colors.BOLD = ''
        Colors.UNDERLINE = ''


def print_header(text: str):
    """Print a formatted header"""
    width = 70
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * width}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text.center(width)}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * width}{Colors.ENDC}\n")


def print_section(text: str):
    """Print a section title"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}▶ {text}{Colors.ENDC}")
    print(f"{Colors.BLUE}{'-' * 50}{Colors.ENDC}\n")


def print_success(text: str):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {text}{Colors.ENDC}")


def print_error(text: str):
    """Print error message"""
    print(f"{Colors.RED}✗ {text}{Colors.ENDC}")


def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.ENDC}")


def print_info(text: str):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ {text}{Colors.ENDC}")


def get_repo_input() -> str:
    """
    Prompt user to input a repository path or URL.
    Validates the input before returning.
    
    Returns:
        str: Valid repository path or URL
    """
    print_section("Repository Input")
    print("Enter the path to your project repository:")
    print(f"{Colors.YELLOW}Examples:{Colors.ENDC}")
    print("  • /path/to/local/project")
    print("  • ./my-project")
    print("  • C:\\Users\\username\\projects\\my-app  (Windows)")
    print("  • https://github.com/user/repo")
    print()

    while True:
        repo_input = input(f"{Colors.BOLD}Repository path or URL: {Colors.ENDC}").strip()

        if not repo_input:
            print_error("Repository path cannot be empty. Please try again.")
            continue

        # Check if it's a local path
        if not repo_input.startswith(('http://', 'https://')):
            repo_path = Path(repo_input).resolve()
            if not repo_path.exists():
                print_error(f"Path does not exist: {repo_path}")
                continue
            if not repo_path.is_dir():
                print_error(f"Path is not a directory: {repo_path}")
                continue
            print_success(f"Repository found: {repo_path}")
            return str(repo_path)
        else:
            # For URLs, do basic validation
            print_success(f"Repository URL accepted: {repo_input}")
            return repo_input


def get_pipeline_options() -> dict:
    """
    Interactive menu to configure pipeline options.
    
    Returns:
        dict: Configuration options for the pipeline
    """
    print_section("Pipeline Configuration")
    
    options = {
        'include_tests': True,
        'include_lint': False,
        'cleanup': False,
        'extra_commands': [],
        'retries': None,
    }

    # Include tests
    print("Include unit tests?")
    print("  1) Yes (recommended)")
    print("  2) No")
    choice = input(f"{Colors.BOLD}Choice [1-2]: {Colors.ENDC}").strip()
    options['include_tests'] = choice != '2'
    print_success(f"Include tests: {options['include_tests']}")

    # Include linting
    print("\nInclude linting?")
    print("  1) Yes")
    print("  2) No (default)")
    choice = input(f"{Colors.BOLD}Choice [1-2]: {Colors.ENDC}").strip()
    options['include_lint'] = choice == '1'
    print_success(f"Include linting: {options['include_lint']}")

    # Cleanup
    print("\nRun cleanup steps after pipeline?")
    print("  1) Yes")
    print("  2) No (default)")
    choice = input(f"{Colors.BOLD}Choice [1-2]: {Colors.ENDC}").strip()
    options['cleanup'] = choice == '1'
    print_success(f"Cleanup: {options['cleanup']}")

    # Retries
    print("\nNumber of retries for failed steps?")
    print("  • Leave empty for default (no retry)")
    retry_input = input(f"{Colors.BOLD}Retries [0-5]: {Colors.ENDC}").strip()
    if retry_input:
        try:
            retries = int(retry_input)
            if 0 <= retries <= 5:
                options['retries'] = retries
                print_success(f"Retries: {retries}")
            else:
                print_warning("Invalid retry count. Using default.")
        except ValueError:
            print_warning("Invalid input. Using default retry count.")

    # Extra commands
    print("\nAdd custom commands?")
    print("  • Leave empty to skip")
    while True:
        cmd = input(f"{Colors.BOLD}Custom command (or press Enter to skip): {Colors.ENDC}").strip()
        if not cmd:
            break
        options['extra_commands'].append(cmd)
        print_success(f"Added command: {cmd}")

    return options


def display_pipeline_summary(project_type: str, steps: list, options: dict):
    """Display a summary of the pipeline before execution"""
    print_section("Pipeline Summary")
    
    print(f"{Colors.BOLD}Project Type:{Colors.ENDC} {project_type}")
    print(f"{Colors.BOLD}Configuration:{Colors.ENDC}")
    print(f"  • Include Tests: {options.get('include_tests', True)}")
    print(f"  • Include Linting: {options.get('include_lint', False)}")
    print(f"  • Cleanup: {options.get('cleanup', False)}")
    print(f"  • Retries: {options.get('retries', 'default')}")
    
    if options.get('extra_commands'):
        print(f"  • Custom Commands: {len(options['extra_commands'])}")
    
    print(f"\n{Colors.BOLD}Pipeline Steps:{Colors.ENDC}")
    for idx, step in enumerate(steps, 1):
        retry_info = f" (retry: {step.retry})" if step.retry > 0 else ""
        print(f"  {idx}. {step.name}{retry_info}")
        print(f"     Command: {Colors.YELLOW}{step.command}{Colors.ENDC}")


def display_execution_results(results: list):
    """Display the results of pipeline execution"""
    print_section("Pipeline Execution Results")
    
    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed
    
    print(f"{Colors.BOLD}Summary:{Colors.ENDC}")
    print(f"  • Total Steps: {len(results)}")
    print_success(f"Passed: {passed}")
    if failed > 0:
        print_error(f"Failed: {failed}")
    
    print(f"\n{Colors.BOLD}Detailed Results:{Colors.ENDC}\n")
    
    for idx, result in enumerate(results, 1):
        status_symbol = "✓" if result.success else "✗"
        status_color = Colors.GREEN if result.success else Colors.RED
        
        print(f"{status_color}{status_symbol} Step {idx}: {result.step.name}{Colors.ENDC}")
        print(f"  Attempts: {result.attempts}")
        
        if result.output:
            output_lines = result.output.strip().split('\n')
            if len(output_lines) > 5:
                print(f"  Output (last 5 lines):")
                for line in output_lines[-5:]:
                    print(f"    {line[:80]}")
            else:
                print(f"  Output:")
                for line in output_lines:
                    print(f"    {line[:80]}")
        print()
    
    if failed == 0:
        print_success(f"All {len(results)} steps completed successfully!")
    else:
        print_error(f"{failed} step(s) failed. Check the output above for details.")


def confirm_action(prompt: str) -> bool:
    """Prompt user for yes/no confirmation"""
    while True:
        response = input(f"{Colors.BOLD}{prompt} [y/n]: {Colors.ENDC}").strip().lower()
        if response in ('y', 'yes'):
            return True
        elif response in ('n', 'no'):
            return False
        else:
            print_error("Please enter 'y' or 'n'.")


def display_welcome():
    """Display welcome screen"""
    print_header("CI/CD Orchestrator Agent")
    print(textwrap.dedent(f"""
        {Colors.BOLD}Welcome to the CI/CD Orchestrator!{Colors.ENDC}
        
        This tool helps you:
        • Detect your project type automatically
        • Generate optimized CI/CD pipelines
        • Execute pipelines with retry support
        • Track build and test results
        
        {Colors.YELLOW}Let's get started!{Colors.ENDC}
    """))


def display_menu() -> str:
    """Display main menu and get user choice"""
    print_section("Main Menu")
    print("What would you like to do?")
    print("  1) Run pipeline locally")
    print("  2) Detect project type only")
    print("  3) View pipeline without running")
    print("  4) Deploy to Azure DevOps (create & trigger pipeline automatically)")
    print("  5) Exit")

    while True:
        choice = input(f"{Colors.BOLD}Select [1-5]: {Colors.ENDC}").strip()
        if choice in ('1', '2', '3', '4', '5'):
            return choice
        print_error("Invalid choice. Please enter 1-5.")


def get_ado_credentials() -> dict:
    """
    Interactively collect Azure DevOps credentials and pipeline settings.

    Returns a dict with keys: org_url, project, pat, repo_name, branch, pipeline_name.
    """
    print_section("Azure DevOps Configuration")
    print("Enter your Azure DevOps details to create and trigger the pipeline.\n")

    org_url = input(
        f"{Colors.BOLD}Org URL (e.g. https://dev.azure.com/myorg): {Colors.ENDC}"
    ).strip()
    project = input(f"{Colors.BOLD}Project Name: {Colors.ENDC}").strip()
    pat = input(
        f"{Colors.BOLD}Personal Access Token (needs Code Read+Write & Build Read+Execute): {Colors.ENDC}"
    ).strip()
    repo_name = input(f"{Colors.BOLD}Repository Name: {Colors.ENDC}").strip()

    branch_input = input(f"{Colors.BOLD}Branch [main]: {Colors.ENDC}").strip()
    branch = branch_input or "main"

    name_input = input(f"{Colors.BOLD}Pipeline Name [CI Pipeline]: {Colors.ENDC}").strip()
    pipeline_name = name_input or "CI Pipeline"

    return {
        "org_url": org_url,
        "project": project,
        "pat": pat,
        "repo_name": repo_name,
        "branch": branch,
        "pipeline_name": pipeline_name,
    }


def display_project_detection(project_type: str):
    """Display detected project type"""
    print_section("Project Detection Result")
    print(f"{Colors.BOLD}Detected Project Type:{Colors.ENDC} {Colors.GREEN}{project_type}{Colors.ENDC}")
    print()


def show_error_message(error_msg: str):
    """Display an error message"""
    print_section("Error")
    print_error(error_msg)


def show_success_message(msg: str):
    """Display a success message"""
    print_section("Success")
    print_success(msg)
