"""Guard the migration files required by a fresh container installation."""

from pathlib import Path


def test_app_image_dockerfile_includes_alembic_config_and_revision_graph() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    ignored = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "COPY alembic.ini ./alembic.ini" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert "alembic.ini" not in ignored
    assert "migrations" not in ignored
    assert "migrations/" not in ignored
    assert (root / "alembic.ini").is_file()
    assert (root / "migrations" / "env.py").is_file()
    assert list((root / "migrations" / "versions").glob("*.py"))
