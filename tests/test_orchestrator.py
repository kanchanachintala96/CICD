import json
import tempfile
from pathlib import Path

from cicd_orchestrator.pipeline import PipelineOptions, PipelineStage, build_pipeline
from cicd_orchestrator.project_detector import detect_project_type, detect_project_info
from cicd_orchestrator.pipeline_exporter import export_pipeline


# ── project detection ──────────────────────────────────────────────────────

def test_detect_python_by_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\n')
    assert detect_project_type(tmp_path) == "python"


def test_detect_nodejs(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"app","scripts":{"test":"jest"}}')
    assert detect_project_type(tmp_path) == "nodejs"


def test_detect_java_maven(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    assert detect_project_type(tmp_path) == "java-maven"


def test_detect_java_gradle(tmp_path):
    (tmp_path / "build.gradle").write_text("")
    assert detect_project_type(tmp_path) == "java-gradle"


def test_detect_go(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
    assert detect_project_type(tmp_path) == "go"


def test_detect_dotnet(tmp_path):
    (tmp_path / "App.csproj").write_text("<Project/>")
    assert detect_project_type(tmp_path) == "dotnet"


def test_detect_unknown(tmp_path):
    assert detect_project_type(tmp_path) == "unknown"


def test_detect_project_info_has_keys(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\n')
    info = detect_project_info(tmp_path)
    assert "type" in info
    assert "has_dockerfile" in info
    assert "has_tests" in info


# ── pipeline building ──────────────────────────────────────────────────────

def test_python_pipeline_has_test_step(tmp_path):
    pipeline = build_pipeline("python", str(tmp_path))
    stages = [s.stage for s in pipeline.steps]
    assert PipelineStage.TEST in stages


def test_python_pipeline_stage_sequence(tmp_path):
    pipeline = build_pipeline("python", str(tmp_path))
    stages = [s.stage.value for s in pipeline.steps]
    # Build should come before test
    assert stages.index("build") < stages.index("test")


def test_nodejs_pipeline(tmp_path):
    pkg = {"name": "app", "scripts": {"build": "tsc", "test": "jest"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    pipeline = build_pipeline("nodejs", str(tmp_path))
    cmds = [s.command for s in pipeline.steps]
    assert any("npm" in c for c in cmds)
    assert any("jest" in c or "npm test" in c for c in cmds)


def test_go_pipeline(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
    pipeline = build_pipeline("go", str(tmp_path), PipelineOptions(include_tests=True))
    cmds = [s.command for s in pipeline.steps]
    assert any("go build" in c for c in cmds)
    assert any("go test" in c for c in cmds)


def test_deploy_stage_docker(tmp_path):
    options = PipelineOptions(
        deploy_type="docker",
        docker_image="myorg/app",
        deploy_env="staging",
    )
    pipeline = build_pipeline("python", str(tmp_path), options)
    deploy = pipeline.deploy_steps
    assert len(deploy) >= 1
    assert any("docker" in s.command for s in deploy)


def test_deploy_stage_kubernetes(tmp_path):
    options = PipelineOptions(deploy_type="kubernetes", k8s_manifest="k8s/", deploy_env="production")
    pipeline = build_pipeline("python", str(tmp_path), options)
    assert any("kubectl" in s.command for s in pipeline.deploy_steps)


def test_pipeline_project_type_field(tmp_path):
    pipeline = build_pipeline("python", str(tmp_path))
    assert pipeline.project_type == "python"


# ── pipeline export ────────────────────────────────────────────────────────

def test_github_actions_export(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    pipeline = build_pipeline("python", str(tmp_path), PipelineOptions(include_tests=True))
    yaml_out = export_pipeline(pipeline, "github-actions")
    assert "push" in yaml_out
    assert "build" in yaml_out


def test_gitlab_ci_export(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    pipeline = build_pipeline("python", str(tmp_path))
    yaml_out = export_pipeline(pipeline, "gitlab-ci")
    assert "stages" in yaml_out


def test_jenkinsfile_export(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    pipeline = build_pipeline("python", str(tmp_path))
    jf = export_pipeline(pipeline, "jenkins")
    assert "pipeline {" in jf
    assert "stage(" in jf


def test_export_unknown_target():
    pipeline = build_pipeline("python", "sample_app")
    try:
        export_pipeline(pipeline, "nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


# ── RAG tests ─────────────────────────────────────────────────────────────────

def test_rag_reads_requirements(tmp_path):
    from cicd_orchestrator.rag import build_rag_context
    (tmp_path / "requirements.txt").write_text("flask\npytest\n")
    result = build_rag_context(str(tmp_path), token_budget=2000)
    assert "requirements.txt" in result["files_read"]
    assert "flask" in result["context"]
    assert result["estimated_tokens"] > 0


def test_rag_respects_token_budget(tmp_path):
    from cicd_orchestrator.rag import build_rag_context
    # Big file that should be truncated
    (tmp_path / "README.md").write_text("x" * 50000)
    result = build_rag_context(str(tmp_path), token_budget=500)
    # Should not exceed budget dramatically
    assert result["estimated_tokens"] <= 700  # some slack


def test_rag_reads_package_json(tmp_path):
    from cicd_orchestrator.rag import build_rag_context
    import json
    (tmp_path / "package.json").write_text(json.dumps({"name": "myapp", "scripts": {"test": "jest"}}))
    result = build_rag_context(str(tmp_path))
    assert "package.json" in result["files_read"]


def test_estimate_tokens():
    from cicd_orchestrator.rag import estimate_tokens
    t = estimate_tokens("Hello world, this is a test sentence.")
    assert t > 0


# ── LLM agent structure tests (no actual LLM calls) ──────────────────────────

def test_llm_agent_raises_on_bad_provider(tmp_path):
    from cicd_orchestrator.llm_agent import analyze_with_llm
    try:
        analyze_with_llm(str(tmp_path), provider="nonexistent-provider")
        assert False, "Should raise"
    except ValueError:
        pass


