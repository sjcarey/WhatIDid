import pytest

from whatidid.config import load_config
from whatidid.store import Store


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATIDID_CONFIG", str(tmp_path / "none.toml"))
    return load_config(
        overrides={
            "storage.data_dir": str(tmp_path / "data"),
            "storage.host": "testhost",
            "tags.aliases": {"neos": "neo-surveyor"},
            "tags.keywords": {"meetings": ["meeting", "telecon"]},
        }
    )


@pytest.fixture
def store(cfg):
    return Store.from_config(cfg)
