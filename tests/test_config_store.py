import json
from pathlib import Path

import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

import src.config_store as cs


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_PATH to a temp directory for isolation."""
    config_path = tmp_path / ".job_finder" / "config.json"
    monkeypatch.setattr(cs, "CONFIG_PATH", config_path)
    return config_path


def test_load_config_returns_none_when_absent(tmp_config):
    assert cs.load_config() is None


def test_load_config_returns_none_on_invalid_json(tmp_config):
    tmp_config.parent.mkdir(parents=True)
    tmp_config.write_text("not valid json", encoding="utf-8")
    assert cs.load_config() is None


def test_save_and_load_roundtrip(tmp_config):
    data = {"target_role": "Engineer", "run_interval_days": 3}
    cs.save_config(data)
    assert cs.load_config() == data


def test_save_creates_parent_directory(tmp_config):
    assert not tmp_config.parent.exists()
    cs.save_config({"key": "value"})
    assert tmp_config.exists()


def test_save_is_atomic_no_tmp_left_behind(tmp_config):
    cs.save_config({"x": 1})
    tmp_file = tmp_config.with_suffix(".tmp")
    assert not tmp_file.exists()


def test_save_overwrites_existing_config(tmp_config):
    cs.save_config({"a": 1})
    cs.save_config({"b": 2})
    assert cs.load_config() == {"b": 2}


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

# Strategy: generate valid email addresses (local@domain.tld)
_valid_email_strategy = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    local=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="._+-"),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s[0] not in ".+-" and s[-1] not in ".+-"),
    domain=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-"),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s[0] != "-" and s[-1] != "-"),
    tld=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu")),
        min_size=2,
        max_size=6,
    ),
)


@given(email=_valid_email_strategy)
@settings(max_examples=100, deadline=None)
def test_recipient_email_round_trip(email: str):
    """Property 2: recipient_email round-trips through config save/load.

    For any valid email address string, saving a config dict containing that
    address via save_config() and then loading it via load_config() SHALL
    return a dict whose recipient_email field equals the original address.

    **Validates: Requirements 1.4, 4.3**
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / ".job_finder" / "config.json"
        original_path = cs.CONFIG_PATH
        cs.CONFIG_PATH = config_path
        try:
            config = {
                "target_role": "Software Engineer",
                "run_interval_days": 1,
                "recipient_email": email,
            }
            cs.save_config(config)
            loaded = cs.load_config()
        finally:
            cs.CONFIG_PATH = original_path

    assert loaded is not None, "load_config() returned None after save_config()"
    assert loaded["recipient_email"] == email, (
        f"Expected recipient_email {email!r}, got {loaded.get('recipient_email')!r}"
    )
