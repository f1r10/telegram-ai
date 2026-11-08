"""The bootstrap-flag repair must stay additive, idempotent and admin-aware."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/0024_bootstrap_flag_repair.py"


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function(name: str) -> ast.FunctionDef:
    for node in ast.parse(_source()).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing {name}() in the repair migration")


def _string_constants() -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in ast.parse(_source()).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                constants[node.target.id] = value.value
    return constants


def test_repair_migration_chains_after_photo_groups() -> None:
    constants = _string_constants()
    assert constants["revision"] == "0024_bootstrap_flag_repair"
    assert constants["down_revision"] == "0023_photo_groups"


def test_repair_is_the_single_head_after_photo_groups() -> None:
    versions = ROOT / "migrations/versions"
    children = [
        path.name
        for path in versions.glob("*.py")
        if 'down_revision: str | None = "0023_photo_groups"' in path.read_text(encoding="utf-8")
    ]
    assert children == [MIGRATION.name]


def test_repair_insert_is_additive_idempotent_and_admin_aware() -> None:
    source = ast.get_source_segment(_source(), _function("upgrade"))
    assert source is not None
    assert "INSERT INTO system_metadata (key, value)" in source
    assert "ON CONFLICT (key) DO NOTHING" in source
    # An existing admin must never allow a second bootstrap admin to be created.
    assert "CASE WHEN EXISTS (SELECT 1 FROM users WHERE role = 'ADMIN')" in source
    assert not re.search(r"\b(UPDATE|DELETE|DROP|TRUNCATE)\b", source)


def test_repair_downgrade_never_removes_bootstrap_state() -> None:
    source = ast.get_source_segment(_source(), _function("downgrade"))
    assert source is not None
    assert "op.execute" not in source
