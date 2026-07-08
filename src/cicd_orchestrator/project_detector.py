import json
from pathlib import Path
from typing import Any, Dict, Union


SUPPORTED_TYPES = [
    "python", "nodejs", "java-maven", "java-gradle",
    "go", "docker", "dotnet", "ruby", "unknown",
]


def detect_project_type(path: Union[str, Path] = ".") -> str:
    """Detect the primary project type by inspecting well-known indicator files."""
    root = Path(path)
    if not root.exists() or not root.is_dir():
        return "unknown"

    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists():
        return "python"
    if any(root.glob("*.py")):
        return "python"

    if (root / "package.json").exists():
        return "nodejs"

    if (root / "pom.xml").exists():
        return "java-maven"

    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return "java-gradle"

    if (root / "go.mod").exists():
        return "go"

    if any(root.glob("*.csproj")) or any(root.glob("*.sln")):
        return "dotnet"

    if (root / "Gemfile").exists():
        return "ruby"

    if (root / "Dockerfile").exists():
        return "docker"

    return "unknown"


def detect_project_info(path: Union[str, Path] = ".") -> Dict[str, Any]:
    """Return a richer dict describing the project at *path*."""
    root = Path(path)
    project_type = detect_project_type(root)

    has_build_script = _detect_build_script(root, project_type)
    has_tests = _detect_tests(root, project_type)
    has_lint_config = _detect_lint(root, project_type)

    return {
        "type": project_type,
        "path": str(root.resolve()),
        "has_dockerfile": (root / "Dockerfile").exists(),
        "has_docker_compose": (
            (root / "docker-compose.yml").exists()
            or (root / "docker-compose.yaml").exists()
        ),
        "has_build_script": has_build_script,
        "has_tests": has_tests,
        "has_lint_config": has_lint_config,
        "nodejs_scripts": _get_nodejs_scripts(root) if project_type == "nodejs" else {},
    }


# ── helpers ────────────────────────────────────────────────────────────────

def _get_nodejs_scripts(root: Path) -> Dict[str, str]:
    pkg = root / "package.json"
    if not pkg.exists():
        return {}
    try:
        return json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    except Exception:
        return {}


def _detect_build_script(root: Path, project_type: str) -> bool:
    if project_type == "nodejs":
        return "build" in _get_nodejs_scripts(root)
    if project_type in ("java-maven", "java-gradle"):
        return True
    if project_type == "go":
        return True
    return False


def _detect_tests(root: Path, project_type: str) -> bool:
    if project_type == "python":
        return (
            (root / "tests").exists()
            or any(root.glob("test_*.py"))
            or any(root.glob("*_test.py"))
        )
    if project_type == "nodejs":
        return "test" in _get_nodejs_scripts(root)
    if project_type in ("java-maven", "java-gradle"):
        return (root / "src" / "test").exists()
    if project_type == "go":
        return any(root.glob("**/*_test.go"))
    if project_type == "dotnet":
        return any(root.glob("**/*Tests*.csproj")) or any(root.glob("**/*Test*.csproj"))
    if project_type == "ruby":
        return (root / "spec").exists() or (root / "test").exists()
    return False


def _detect_lint(root: Path, project_type: str) -> bool:
    if project_type == "python":
        return any([
            (root / ".flake8").exists(),
            (root / "setup.cfg").exists(),
            (root / ".pylintrc").exists(),
            (root / "pyproject.toml").exists(),
        ])
    if project_type == "nodejs":
        return any([
            (root / ".eslintrc.json").exists(),
            (root / ".eslintrc.js").exists(),
            (root / ".eslintrc.yml").exists(),
            (root / ".eslintrc.yaml").exists(),
            (root / "eslint.config.js").exists(),
        ])
    if project_type == "go":
        return (root / ".golangci.yml").exists() or (root / ".golangci.yaml").exists()
    return False
