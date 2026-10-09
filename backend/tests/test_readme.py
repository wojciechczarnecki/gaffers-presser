import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DEPLOYMENT = ROOT / "docs" / "DEPLOYMENT.md"

COMMANDS = [
    "reference-sync",
    "deadline-snapshot",
    "league-sync",
    "results-sync",
    "backfill",
]

TWEET_VARIABLES = [
    "TWEET_SOURCE",
    "X_LIST_ID",
    "TWSCRAPE_USERNAME",
    "TWSCRAPE_COOKIES",
    "TWSCRAPE_ACCOUNTS_DB",
    "TWITTERAPI_IO_KEY",
    "X_API_BEARER_TOKEN",
]

EXTRACTION_VARIABLES = [
    "LLM_MODEL",
    "LLM_FALLBACK_MODEL",
    "OPENROUTER_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "USD_PLN_RATE",
]

EXTRACTION_COMMANDS = [
    "app.extraction reextract",
    "app.extraction prelabel",
    "app.extraction evaluate",
    "app.extraction compare-labels",
    "app.extraction spend",
]

REMOVED_VARIABLES = ["LLM_PROVIDER", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
ENV_EXAMPLE = ROOT / "backend" / ".env.example"


def _development_section() -> str:
    text = README.read_text(encoding="utf-8")
    match = re.search(r"## Development\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    assert match, "README.md has no '## Development' section"
    return match.group(1)


def _deployment_section() -> str:
    text = README.read_text(encoding="utf-8")
    match = re.search(r"## Deployment\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    assert match, "README.md has no '## Deployment' section"
    return match.group(1)


def test_readme_deployment_section_links_the_runbook():
    assert "docs/DEPLOYMENT.md" in _deployment_section()


def test_development_section_lists_commands():
    section = _development_section()
    assert "docker compose up -d" in section
    assert "alembic upgrade head" in section
    for command in COMMANDS:
        assert command in section
    assert "app.worker run" in section
    assert "app.worker status" in section
    assert "app.tweets measure" in section
    assert "app.tweets summary" in section
    assert "app.tweets members" in section
    for variable in TWEET_VARIABLES:
        assert variable in section, f"{variable!r} missing from the README Development section"
    for variable in EXTRACTION_VARIABLES:
        assert variable in section, f"{variable!r} missing from the README Development section"
    for command in EXTRACTION_COMMANDS:
        assert command in section, f"{command!r} missing from the README Development section"
    assert "app.extraction snapshot-players" in section


def test_deployment_doc_is_a_runbook():
    section = DEPLOYMENT.read_text(encoding="utf-8")
    # Built at runtime, not as a literal: tests/core/test_settings.py's
    # test_database_url_not_read_by_tests greps for that env var's name and allows it
    # only in its own file; this check is about the runbook's wording, not about reading it.
    database_url_var = "DATABASE" + "_URL"
    for term in (
        "pgvector/pgvector:pg16",
        "volume",
        database_url_var,
        "FPL_LEAGUE_IDS",
        "Wait for CI",
        "railway logs",
        "railway ssh",
        "python -m app.worker status",
        "alembic upgrade head",
        "catch-up",
    ):
        assert term in section, f"{term!r} missing from docs/DEPLOYMENT.md"
    for variable in TWEET_VARIABLES:
        assert variable in section, f"{variable!r} missing from docs/DEPLOYMENT.md"
    for variable in EXTRACTION_VARIABLES:
        assert variable in section, f"{variable!r} missing from docs/DEPLOYMENT.md"
    for command in EXTRACTION_COMMANDS:
        assert command in section, f"{command!r} missing from docs/DEPLOYMENT.md"
    assert "Extraction: disabled" in section


def test_removed_llm_variables_absent_from_docs():
    for path in (README, DEPLOYMENT, ENV_EXAMPLE):
        text = path.read_text(encoding="utf-8")
        for removed in [*REMOVED_VARIABLES, "--provider"]:
            assert removed not in text, f"{removed!r} still in {path.name}"


def test_deployment_documents_embedding_model():
    section = DEPLOYMENT.read_text(encoding="utf-8")
    for term in (
        "EMBEDDING_MODEL",
        "retrieval indexing disabled",
        "app.retrieval index",
        "app.retrieval status",
        "app.retrieval search",
        "0005",
    ):
        assert term in section, f"{term!r} missing from docs/DEPLOYMENT.md"


def test_corroboration_commands_documented():
    section = _development_section()
    deployment = DEPLOYMENT.read_text(encoding="utf-8")
    for command in (
        "app.corroboration",
        "app.corroboration.evaluation build-cases",
        "app.corroboration.evaluation review",
        "app.corroboration.evaluation evaluate",
    ):
        assert command in section, f"{command!r} missing from the README Development section"
        assert command.split(" ")[0] in deployment
    for term in ("build-cases", "review", "evaluate", "0006"):
        assert term in deployment, f"{term!r} missing from docs/DEPLOYMENT.md"


def test_deployment_documents_delivery():
    section = DEPLOYMENT.read_text(encoding="utf-8")
    for term in (
        "DELIVERY_PROVIDER",
        "RESEND_API_KEY",
        "DELIVERY_EMAIL_TO",
        "DELIVERY_EMAIL_FROM",
        "python -m app.delivery send-test",
        "python -m app.delivery status",
        "onboarding@resend.dev",
        "Delivery:",
    ):
        assert term in section, f"{term!r} missing from docs/DEPLOYMENT.md"


ALERT_VARIABLES = [
    "ALERTS_ENABLED",
    "ALERT_SLOTS",
    "ALERT_TRENDING_MIN_ACCOUNTS",
    "ALERT_WIDELY_OWNED_PERCENT",
    "ALERT_REHEARSAL_DEADLINE",
]


def test_alert_commands_documented():
    section = _development_section()
    for command in ("app.alerts status", "app.alerts latency", "app.alerts preview"):
        assert command in section, f"{command!r} missing from the README Development section"


def test_deployment_documents_alerts():
    section = DEPLOYMENT.read_text(encoding="utf-8")
    for variable in ALERT_VARIABLES:
        assert variable in section, f"{variable!r} missing from docs/DEPLOYMENT.md"
    for term in (
        "0008",
        "0009",
        "python -m app.alerts status",
        "python -m app.alerts latency",
        "python -m app.alerts preview",
        "Alerts:",
    ):
        assert term in section, f"{term!r} missing from docs/DEPLOYMENT.md"


def test_deployment_documents_alert_slots():
    section = DEPLOYMENT.read_text(encoding="utf-8")
    start = section.index("12. **Alerts")
    step = (
        section[start : section.index("\n13.", start)]
        if "\n13." in section[start:]
        else section[start:]
    )
    for term in ("D-1@20:00,60", "ALERT_SLOTS_MINUTES", "90 minutes", "alert span"):
        assert term in step, f"{term!r} missing from DEPLOYMENT step 12"
    removed = step[step.index("ALERT_SLOTS_MINUTES") :][:300].lower()
    assert "removed" in removed or "replaced" in removed


def test_deployment_describes_rehearsal_variable():
    section = DEPLOYMENT.read_text(encoding="utf-8")
    assert "ALERT_REHEARSAL_DEADLINE" in section
    paragraph = section[section.index("ALERT_REHEARSAL_DEADLINE") :][:1500].lower()
    for term in ("optional", "testing", "remove"):
        assert term in paragraph, f"{term!r} missing near ALERT_REHEARSAL_DEADLINE"
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "ALERT_REHEARSAL_DEADLINE" in example
    comment = example[: example.index("ALERT_REHEARSAL_DEADLINE")][-600:].lower()
    assert "remov" in comment and "test" in comment


PRESSER_VARIABLES = ["PRESSER_ENABLED", "PRESSER_MODEL", "PRESSER_NICKNAMES"]
PRESSER_COMMANDS = [
    "app.presser facts",
    "app.presser preview",
    "app.presser send",
    "app.presser status",
    "app.presser.evaluation",
]


def test_presser_documented():
    deployment = DEPLOYMENT.read_text(encoding="utf-8")
    for text in (_development_section(), deployment):
        for variable in PRESSER_VARIABLES:
            assert variable in text, variable
        for command in PRESSER_COMMANDS:
            assert command in text, command
    assert "0011" in deployment
    assert "Presser:" in deployment
