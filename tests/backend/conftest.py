import json
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture
def load_fixture():
    def load(name):
        return json.loads((FIXTURES / name).read_text())

    return load


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "ytm-config"
    monkeypatch.setenv("YTM_CONFIG_DIR", str(path))
    return path


@pytest.fixture(autouse=True)
def private_config_dir(tmp_path, monkeypatch):
    # S16: and never read the real session or identity (~/.config/omarchy-ytmusic);
    # the config_dir fixture names the same directory.
    monkeypatch.setenv("YTM_CONFIG_DIR", str(tmp_path / "ytm-config"))


@pytest.fixture(autouse=True)
def private_cache_dir(tmp_path, monkeypatch):
    # A Worker built without a cache uses the real one; tests must never read,
    # clear or record into ~/.cache/omarchy-ytmusic.
    path = tmp_path / "ytm-cache"
    monkeypatch.setenv("YTM_CACHE_DIR", str(path))
    return path
