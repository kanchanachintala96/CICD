from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class PipelineStage(str, Enum):
    BUILD = "build"
    TEST = "test"
    DEPLOY = "deploy"
    CLEANUP = "cleanup"


@dataclass
class PipelineOptions:
    include_tests: bool = True
    include_lint: bool = False
    cleanup: bool = False
    extra_commands: List[str] = field(default_factory=list)
    retry: Optional[int] = None
    # Deploy options
    deploy_env: Optional[str] = None
    deploy_type: Optional[str] = None
    docker_image: Optional[str] = None
    docker_registry: Optional[str] = None
    deploy_script: Optional[str] = None
    k8s_manifest: Optional[str] = None


@dataclass
class PipelineStep:
    name: str
    command: str
    retry: int = 0
    stage: PipelineStage = PipelineStage.BUILD
    allow_failure: bool = False
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class Pipeline:
    project_type: str = "unknown"
    steps: List[PipelineStep] = field(default_factory=list)
    cleanup_steps: List[PipelineStep] = field(default_factory=list)

    @property
    def build_steps(self) -> List[PipelineStep]:
        return [s for s in self.steps if s.stage == PipelineStage.BUILD]

    @property
    def test_steps(self) -> List[PipelineStep]:
        return [s for s in self.steps if s.stage == PipelineStage.TEST]

    @property
    def deploy_steps(self) -> List[PipelineStep]:
        return [s for s in self.steps if s.stage == PipelineStage.DEPLOY]


# ── language-specific builders ─────────────────────────────────────────────

def _python_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    steps: List[PipelineStep] = []

    if (root / "setup.py").exists() or (root / "pyproject.toml").exists():
        steps.append(PipelineStep(
            name="Install package (editable)",
            command="python -m pip install -e .",
            stage=PipelineStage.BUILD, retry=1,
        ))
    elif (root / "requirements.txt").exists():
        steps.append(PipelineStep(
            name="Install requirements",
            command="python -m pip install -r requirements.txt",
            stage=PipelineStage.BUILD, retry=1,
        ))
    else:
        steps.append(PipelineStep(
            name="Upgrade pip",
            command="python -m pip install --upgrade pip",
            stage=PipelineStage.BUILD, retry=1,
        ))

    if options.include_lint:
        steps.append(PipelineStep(
            name="Lint (flake8)",
            command="python -m pip install flake8 && python -m flake8 .",
            stage=PipelineStage.BUILD,
        ))

    steps.append(PipelineStep(
        name="Compile Python sources",
        command="python -m compileall .",
        stage=PipelineStage.BUILD,
    ))

    if options.include_tests:
        steps.append(PipelineStep(
            name="Run tests (pytest)",
            command="python -m pytest tests -v",
            stage=PipelineStage.TEST,
        ))

    return steps


def _nodejs_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    import json
    steps: List[PipelineStep] = []

    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        scripts = pkg.get("scripts", {})
    except Exception:
        scripts = {}

    install_cmd = "npm ci" if (root / "package-lock.json").exists() else "npm install"
    steps.append(PipelineStep(
        name="Install dependencies (npm)",
        command=install_cmd,
        stage=PipelineStage.BUILD, retry=1,
    ))

    if options.include_lint and "lint" in scripts:
        steps.append(PipelineStep(
            name="Lint (eslint)",
            command="npm run lint",
            stage=PipelineStage.BUILD,
        ))

    if "build" in scripts:
        steps.append(PipelineStep(
            name="Build application",
            command="npm run build",
            stage=PipelineStage.BUILD,
        ))

    if options.include_tests and "test" in scripts:
        steps.append(PipelineStep(
            name="Run tests",
            command="npm test",
            stage=PipelineStage.TEST,
        ))

    return steps


def _maven_cmd(root: Path) -> str:
    """Return the Maven command for this project.

    ./mvnw is only used when the Unix wrapper actually exists in the repo —
    ADO agents run ubuntu-latest (Linux) so mvnw.cmd is useless there.
    If only mvnw.cmd exists, fall back to system 'mvn' (pre-installed on ubuntu-latest).
    orchestrator.py substitutes the full path to mvnw.cmd for local Windows runs.
    """
    if (root / "mvnw").exists():
        return "./mvnw"
    return "mvn"


def _java_maven_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    mvn = _maven_cmd(root)   # ./mvnw when wrapper exists; mvn otherwise
    base_flags = "--no-transfer-progress"
    steps: List[PipelineStep] = [
        PipelineStep(
            name="Build (Maven)",
            command=f"{mvn} clean package -DskipTests {base_flags}",
            stage=PipelineStage.BUILD, retry=1,
        ),
    ]
    if options.include_tests:
        steps.append(PipelineStep(
            name="Run tests (Maven)",
            command=f"{mvn} test {base_flags}",
            stage=PipelineStage.TEST,
        ))
    return steps


def _java_gradle_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    gradle_cmd = "./gradlew" if (root / "gradlew").exists() else "gradle"
    steps: List[PipelineStep] = [
        PipelineStep(
            name="Build (Gradle)",
            command=f"{gradle_cmd} assemble",
            stage=PipelineStage.BUILD, retry=1,
        ),
    ]
    if options.include_tests:
        steps.append(PipelineStep(
            name="Run tests (Gradle)",
            command=f"{gradle_cmd} test",
            stage=PipelineStage.TEST,
        ))
    return steps


def _go_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    steps: List[PipelineStep] = [
        PipelineStep(
            name="Download modules",
            command="go mod download",
            stage=PipelineStage.BUILD, retry=1,
        ),
        PipelineStep(
            name="Build (go build)",
            command="go build ./...",
            stage=PipelineStage.BUILD,
        ),
    ]
    if options.include_lint:
        steps.append(PipelineStep(
            name="Vet (go vet)",
            command="go vet ./...",
            stage=PipelineStage.BUILD,
        ))
    if options.include_tests:
        steps.append(PipelineStep(
            name="Run tests (go test)",
            command="go test ./... -v",
            stage=PipelineStage.TEST,
        ))
    return steps


def _dotnet_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    steps: List[PipelineStep] = [
        PipelineStep(
            name="Restore packages (.NET)",
            command="dotnet restore",
            stage=PipelineStage.BUILD, retry=1,
        ),
        PipelineStep(
            name="Build solution (.NET)",
            command="dotnet build --no-restore --configuration Release",
            stage=PipelineStage.BUILD,
        ),
    ]
    if options.include_tests:
        steps.append(PipelineStep(
            name="Run tests (dotnet test)",
            command="dotnet test --no-build --configuration Release",
            stage=PipelineStage.TEST,
        ))
    return steps


def _ruby_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    steps: List[PipelineStep] = [
        PipelineStep(
            name="Install gems (bundle)",
            command="bundle install",
            stage=PipelineStage.BUILD, retry=1,
        ),
    ]
    if options.include_tests:
        steps.append(PipelineStep(
            name="Run tests (rspec)",
            command="bundle exec rspec",
            stage=PipelineStage.TEST,
        ))
    return steps


def _docker_steps(root: Path, options: "PipelineOptions") -> List[PipelineStep]:
    steps: List[PipelineStep] = [
        PipelineStep(
            name="Build Docker image",
            command="docker build -t app:latest .",
            stage=PipelineStage.BUILD,
        ),
    ]
    if options.include_tests:
        steps.append(PipelineStep(
            name="Run container smoke test",
            command='docker run --rm app:latest echo "Container OK"',
            stage=PipelineStage.TEST,
        ))
    return steps


# ── deploy stage ───────────────────────────────────────────────────────────

def _deploy_steps(options: PipelineOptions) -> List[PipelineStep]:
    steps: List[PipelineStep] = []
    if not options.deploy_type:
        return steps

    env_tag = options.deploy_env or "staging"

    if options.deploy_type == "docker":
        image = options.docker_image or "app"
        registry = options.docker_registry or ""
        full_image = f"{registry}/{image}:{env_tag}" if registry else f"{image}:{env_tag}"
        steps += [
            PipelineStep(
                name="Docker build & tag",
                command=f"docker build -t {full_image} .",
                stage=PipelineStage.DEPLOY,
                allow_failure=True,
            ),
            PipelineStep(
                name="Docker push",
                command=f"docker push {full_image}",
                stage=PipelineStage.DEPLOY,
                allow_failure=True,
                env={"DOCKER_REGISTRY": registry or "docker.io"},
            ),
        ]

    elif options.deploy_type == "kubernetes":
        manifest = options.k8s_manifest or "k8s/"
        steps += [
            PipelineStep(
                name=f"Deploy to Kubernetes ({env_tag})",
                command=f"kubectl apply -f {manifest}",
                stage=PipelineStage.DEPLOY,
                allow_failure=True,
            ),
            PipelineStep(
                name="Verify rollout",
                command="kubectl rollout status deployment/app",
                stage=PipelineStage.DEPLOY,
                allow_failure=True,
            ),
        ]

    elif options.deploy_type == "script":
        script = options.deploy_script or "./deploy.sh"
        steps.append(PipelineStep(
            name=f"Run deploy script ({env_tag})",
            command=f"{script} {env_tag}",
            stage=PipelineStage.DEPLOY,
            allow_failure=True,
        ))

    return steps


def _cleanup_steps(project_type: str, options: PipelineOptions, root: Path = Path(".")) -> List[PipelineStep]:
    if not options.cleanup:
        return []
    if project_type == "python":
        return [PipelineStep(
            name="Cleanup pip cache",
            command="python -m pip cache purge",
            stage=PipelineStage.CLEANUP,
        )]
    if project_type == "nodejs":
        return [PipelineStep(
            name="Clean npm cache",
            command="npm cache clean --force",
            stage=PipelineStage.CLEANUP,
        )]
    if project_type == "java-maven":
        mvn = _maven_cmd(root)
        return [PipelineStep(
            name="Cleanup Maven target",
            command=f"{mvn} clean --no-transfer-progress",
            stage=PipelineStage.CLEANUP,
        )]
    return []


# ── public API ─────────────────────────────────────────────────────────────

_BUILDERS = {
    "python": _python_steps,
    "nodejs": _nodejs_steps,
    "java-maven": _java_maven_steps,
    "java-gradle": _java_gradle_steps,
    "go": _go_steps,
    "dotnet": _dotnet_steps,
    "ruby": _ruby_steps,
    "docker": _docker_steps,
}


def build_pipeline(
    project_type: str,
    project_path: str = ".",
    options: Optional[PipelineOptions] = None,
) -> Pipeline:
    if options is None:
        options = PipelineOptions()

    root = Path(project_path)
    builder = _BUILDERS.get(project_type)

    if builder is None:
        steps: List[PipelineStep] = [PipelineStep(
            name="Unknown project type",
            command='echo "No pipeline available."',
            stage=PipelineStage.BUILD,
        )]
    else:
        steps = builder(root, options)

    for index, command in enumerate(options.extra_commands, start=1):
        steps.append(PipelineStep(
            name=f"Custom command {index}",
            command=command,
            retry=options.retry or 0,
            stage=PipelineStage.BUILD,
        ))

    steps += _deploy_steps(options)
    cleanup = _cleanup_steps(project_type, options, root)

    if options.retry is not None:
        for step in steps + cleanup:
            step.retry = options.retry

    return Pipeline(project_type=project_type, steps=steps, cleanup_steps=cleanup)
