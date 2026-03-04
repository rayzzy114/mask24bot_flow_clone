from __future__ import annotations

import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
CLONE_APP_DIR = PROJECT_DIR / "app"
CANONICAL_APP_DIR = (
    PROJECT_DIR.parent / "Infinity_AdminKit_Reusable_20260207_063702" / "source" / "app"
)

REQUIRED_FILES = [
    "handlers/admin.py",
    "storage.py",
    "keyboards.py",
    "states.py",
    "constants.py",
    "context.py",
    "telegram_helpers.py",
    "utils.py",
    "rates.py",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _callback_tokens(source: str) -> set[str]:
    exact = set(re.findall(r'F\.data\s*==\s*"([^"]+)"', source))
    prefixes = set(re.findall(r'F\.data\.startswith\("([^"]+)"\)', source))
    return exact | {f"{prefix}*" for prefix in prefixes}


def _extract_class_block(source: str, class_name: str) -> str:
    start_match = re.search(rf"^class {class_name}\b.*?:\n", source, re.MULTILINE)
    if start_match is None:
        return ""
    start = start_match.end()
    tail = source[start:]
    next_match = re.search(r"^class [A-Za-z_][A-Za-z0-9_]*\b.*?:\n", tail, re.MULTILINE)
    if next_match is None:
        return tail
    return tail[: next_match.start()]


def _extract_methods(source: str, class_name: str) -> set[str]:
    block = _extract_class_block(source, class_name)
    return set(re.findall(r"^\s+(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\(", block, re.MULTILINE))


def _extract_state_names(source: str, class_name: str) -> set[str]:
    block = _extract_class_block(source, class_name)
    return set(re.findall(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*State\(", block, re.MULTILINE))


def test_adminkit_file_manifest_parity() -> None:
    for rel in REQUIRED_FILES:
        assert (CANONICAL_APP_DIR / rel).exists(), rel
        assert (CLONE_APP_DIR / rel).exists(), rel


def test_adminkit_router_callbacks_parity() -> None:
    canonical_admin = _read(CANONICAL_APP_DIR / "handlers" / "admin.py")
    clone_admin = _read(CLONE_APP_DIR / "handlers" / "admin.py")
    clone_tokens = _callback_tokens(clone_admin)
    canonical_tokens = _callback_tokens(canonical_admin)
    assert canonical_tokens.issubset(clone_tokens)


def test_adminkit_states_parity() -> None:
    canonical_states = _read(CANONICAL_APP_DIR / "states.py")
    clone_states = _read(CLONE_APP_DIR / "states.py")
    clone_admin_states = _extract_state_names(clone_states, "AdminState")
    canonical_admin_states = _extract_state_names(canonical_states, "AdminState")
    assert canonical_admin_states.issubset(clone_admin_states)


def test_adminkit_storage_api_parity() -> None:
    canonical_storage = _read(CANONICAL_APP_DIR / "storage.py")
    clone_storage = _read(CLONE_APP_DIR / "storage.py")

    for class_name in ("SettingsStore", "UsersStore", "OrdersStore"):
        clone_methods = _extract_methods(clone_storage, class_name)
        canonical_methods = _extract_methods(canonical_storage, class_name)
        assert canonical_methods.issubset(clone_methods)
