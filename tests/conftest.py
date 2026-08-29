import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def manifest_torrebruna() -> dict:
    return json.loads((FIXTURES / "manifest_torrebruna.json").read_text(encoding="utf-8"))


@pytest.fixture
def manifest_guardiabruna() -> dict:
    return json.loads((FIXTURES / "manifest_guardiabruna.json").read_text(encoding="utf-8"))


@pytest.fixture
def html_galleria() -> str:
    return (FIXTURES / "galleria.html").read_text(encoding="utf-8")
