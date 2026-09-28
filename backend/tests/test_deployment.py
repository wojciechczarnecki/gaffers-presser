import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
RAILWAY_JSON = REPO_ROOT / "railway.json"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _dockerfile_text() -> str:
    assert DOCKERFILE.exists(), f"{DOCKERFILE} does not exist"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_runs_as_non_root():
    text = _dockerfile_text()
    user_lines = [line for line in text.splitlines() if line.strip().startswith("USER ")]
    assert user_lines, "Dockerfile has no USER instruction"
    last_user = user_lines[-1].split(maxsplit=1)[1].strip()
    assert last_user not in ("root", "0"), f"Dockerfile runs as {last_user!r}"


def test_dockerfile_starts_the_worker():
    text = _dockerfile_text()
    match = re.search(r"^CMD\s*(\[.*\])\s*$", text, re.MULTILINE)
    assert match, "Dockerfile has no exec-form CMD"
    cmd = json.loads(match.group(1))
    assert cmd == ["python", "-m", "app.worker", "run"]


def test_railway_config():
    assert RAILWAY_JSON.exists(), f"{RAILWAY_JSON} does not exist"
    config = json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))
    assert config["$schema"] == "https://railway.com/railway.schema.json"
    assert config["build"]["builder"] == "DOCKERFILE"
    assert config["build"]["dockerfilePath"] == "backend/Dockerfile"
    assert config["deploy"]["startCommand"] == "python -m app.worker run"
    assert config["deploy"]["preDeployCommand"] == ["alembic upgrade head"]
    assert config["deploy"]["restartPolicyType"] == "ALWAYS"
    assert "healthcheckPath" not in config["deploy"]


def test_ci_builds_image_on_pull_request():
    text = CI_YML.read_text(encoding="utf-8")
    assert "pull_request" in text
    assert "docker build -f backend/Dockerfile" in text
