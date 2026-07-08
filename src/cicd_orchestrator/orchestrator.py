import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .guardrails import get_guardrails
from .pipeline import Pipeline, PipelineStep

_ADO_TIP = "\nTip: use 'Deploy to Azure DevOps' to run this pipeline in the cloud where all tools are pre-installed."

_INSTALL_HINTS = {
    "mvn":    "Install Maven from https://maven.apache.org/install.html and ensure it is on your PATH, or add an mvnw wrapper (mvnw / mvnw.cmd) to your project." + _ADO_TIP,
    "gradle": "Install Gradle from https://gradle.org/install or add a gradlew wrapper to your project." + _ADO_TIP,
    "node":   "Install Node.js from https://nodejs.org." + _ADO_TIP,
    "npm":    "Install Node.js (includes npm) from https://nodejs.org." + _ADO_TIP,
    "go":     "Install Go from https://go.dev/dl." + _ADO_TIP,
    "dotnet": "Install .NET SDK from https://dotnet.microsoft.com/download." + _ADO_TIP,
    "docker": "Install Docker from https://docs.docker.com/get-docker." + _ADO_TIP,
    "kubectl":"Install kubectl from https://kubernetes.io/docs/tasks/tools." + _ADO_TIP,
    "ruby":   "Install Ruby from https://www.ruby-lang.org/en/downloads." + _ADO_TIP,
    "bundle": "Install Bundler with: gem install bundler." + _ADO_TIP,
}

_JAVA_INSTALL_HINT = (
    "Java (JDK) is required but was not found.\n"
    "Install a JDK (e.g. Eclipse Temurin) from https://adoptium.net and set "
    "the JAVA_HOME environment variable, or add Java's bin directory to your PATH."
)

# Common JDK root locations to search on Windows
_WIN_JDK_ROOTS = [
    r"C:\Program Files\Java",
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files\Microsoft",
    r"C:\Program Files\BellSoft",
    r"C:\Program Files\Zulu",
    r"C:\Program Files\Amazon Corretto",
    r"C:\Program Files\ojdkbuild",
]


def _find_java_home() -> Optional[str]:
    """Try to locate a JDK/JRE home directory on the current machine."""
    # 1. Already set in environment
    if os.environ.get("JAVA_HOME"):
        return os.environ["JAVA_HOME"]
    # 2. java.exe on PATH — walk up to find the JDK root
    java_exe = shutil.which("java") or shutil.which("java.exe")
    if java_exe:
        return str(Path(java_exe).parent.parent)
    # 3. Scan well-known Windows install dirs
    if sys.platform == "win32":
        for root in _WIN_JDK_ROOTS:
            root_path = Path(root)
            if root_path.exists():
                for candidate in sorted(root_path.iterdir(), reverse=True):
                    if (candidate / "bin" / "java.exe").exists():
                        return str(candidate)
    return None


def _preflight_check(command: str) -> Optional[str]:
    """Return a helpful error message if the command's tool is not available, else None."""
    base_cmd = command.split()[0]
    # A command is a "wrapper" (project-local script) if it has a path separator,
    # ends with .cmd, or is a known wrapper name — no PATH check needed for these.
    is_wrapper = (
        "\\" in base_cmd
        or "/" in base_cmd
        or base_cmd.endswith(".cmd")
        or base_cmd in ("mvnw", "gradlew")
    )

    # Maven wrapper (mvnw / mvnw.cmd) or plain mvn — also need Java
    if "mvnw" in base_cmd or base_cmd == "mvn":
        if not is_wrapper and shutil.which(base_cmd) is None:
            return f"Tool not found: 'mvn' is not installed or not in PATH.\n{_INSTALL_HINTS['mvn']}"
        if _find_java_home() is None:
            return _JAVA_INSTALL_HINT
        return None

    # Gradle wrapper (gradlew / gradlew.bat) or plain gradle — also need Java
    if "gradlew" in base_cmd or base_cmd == "gradle":
        if not is_wrapper and shutil.which(base_cmd) is None:
            return f"Tool not found: 'gradle' is not installed or not in PATH.\n{_INSTALL_HINTS['gradle']}"
        if _find_java_home() is None:
            return _JAVA_INSTALL_HINT
        return None

    # Full paths and wrapper scripts — skip PATH check
    if is_wrapper:
        return None

    if shutil.which(base_cmd) is None:
        hint = _INSTALL_HINTS.get(base_cmd, f"Please install '{base_cmd}' and ensure it is on your PATH.{_ADO_TIP}")
        return f"Tool not found: '{base_cmd}' is not installed or not in PATH.\n{hint}"
    return None


class ExecutionResult:
    def __init__(self, step: PipelineStep, success: bool, attempts: int, output: str):
        self.step = step
        self.success = success
        self.attempts = attempts
        self.output = output


def configure_logger(log_file: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger("cicd_orchestrator")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


_TOOL_NOT_FOUND = ("not recognized", "not found", "no such file", "command not found")


def execute_step(step: PipelineStep, cwd: str = ".", logger: Optional[logging.Logger] = None) -> ExecutionResult:
    if logger is None:
        logger = configure_logger()

    # ── Windows wrapper resolution (must happen BEFORE preflight) ────────────
    # Substitute mvn/./mvnw with the full path to mvnw.cmd so:
    #   • preflight sees it as a wrapper (skips PATH check)
    #   • cmd.exe can find it by absolute path
    command = step.command
    if sys.platform == "win32":
        from pathlib import Path as _Path
        cwd_path = _Path(cwd)
        if command.startswith(("./mvnw", "mvn ")):
            for wrapper in ("mvnw.cmd", "mvnw"):
                wp = cwd_path / wrapper
                if wp.exists():
                    prefix = "./mvnw" if command.startswith("./mvnw") else "mvn"
                    command = command.replace(prefix, f'"{wp.resolve()}"', 1)
                    break
        elif command.startswith("./gradlew"):
            for wrapper in ("gradlew.bat", "gradlew"):
                wp = cwd_path / wrapper
                if wp.exists():
                    command = command.replace("./gradlew", f'"{wp.resolve()}"', 1)
                    break

    # ── Pre-flight: fail fast with a helpful message if tool isn't available ──
    preflight_error = _preflight_check(command)
    if preflight_error:
        logger.warning("Skipping step '%s': %s", step.name, preflight_error)
        return ExecutionResult(step=step, success=False, attempts=0, output=preflight_error)

    # ── Guardrails: block commands that violate the policy contract ───────────
    guardrail_error = get_guardrails().check_command(command)
    if guardrail_error:
        logger.warning("Blocking step '%s': %s", step.name, guardrail_error)
        return ExecutionResult(step=step, success=False, attempts=0, output=guardrail_error)

    attempts = 0
    last_output = ""

    # Build subprocess environment — inject JAVA_HOME if needed and not already set
    base_cmd = command.split()[0]
    step_env = None
    if "mvnw" in base_cmd or "mvn" in base_cmd or "gradlew" in base_cmd or "gradle" in base_cmd:
        java_home = _find_java_home()
        if java_home and not os.environ.get("JAVA_HOME"):
            step_env = {**os.environ, "JAVA_HOME": java_home}
            logger.info("Injecting JAVA_HOME=%s for step: %s", java_home, step.name)

    while attempts <= step.retry:
        attempts += 1
        logger.info("Executing step: %s (attempt %d)", step.name, attempts)
        process = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            env=step_env,
        )

        last_output = process.stdout + process.stderr
        logger.debug("Command output for %s:\n%s", step.name, last_output)

        if process.returncode == 0:
            logger.info("Step succeeded: %s", step.name)
            return ExecutionResult(step=step, success=True, attempts=attempts, output=last_output)

        # Don't retry if the tool itself is missing — retrying won't help
        if any(msg in last_output.lower() for msg in _TOOL_NOT_FOUND):
            logger.warning("Step failed (tool not found, skipping retries): %s", step.name)
            break

        logger.warning("Step failed: %s (return code %s)", step.name, process.returncode)

    return ExecutionResult(step=step, success=False, attempts=attempts, output=last_output)


def execute_steps(
    steps: List[PipelineStep],
    cwd: str = ".",
    logger: Optional[logging.Logger] = None,
    stop_on_failure: bool = True,
) -> List[ExecutionResult]:
    if logger is None:
        logger = configure_logger()

    results: List[ExecutionResult] = []
    for step in steps:
        result = execute_step(step, cwd=cwd, logger=logger)
        results.append(result)
        if not result.success and not step.allow_failure and stop_on_failure:
            break
    return results


def execute_pipeline(pipeline: Pipeline, cwd: str = ".", logger: Optional[logging.Logger] = None) -> List[ExecutionResult]:
    return execute_steps(pipeline.steps, cwd=cwd, logger=logger)
