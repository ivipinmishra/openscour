"""searxng/start.sh writes SEARCH_PROXIES into a copy of SearXNG's settings."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SCRIPT = Path(__file__).parent.parent / "searxng" / "start.sh"
SETTINGS = Path(__file__).parent.parent / "searxng" / "settings.yml"

pytestmark = [
    pytest.mark.skipif(shutil.which("sh") is None or shutil.which("awk") is None, reason="needs sh and awk"),
    # The package's source distribution doesn't include searxng/.
    pytest.mark.skipif(not SCRIPT.exists(), reason="needs the repository's searxng/ folder"),
]


def run_start(tmp_path: Path, proxies: str | None) -> dict:
    """Run start.sh against a copy of settings.yml; it prints the settings SearXNG would load."""
    source = tmp_path / "settings.yml"
    source.write_text(SETTINGS.read_text())
    script = (
        SCRIPT.read_text()
        .replace("/etc/searxng/settings.yml", str(source))
        .replace("/tmp/settings-with-proxies.yml", str(tmp_path / "out.yml"))
        .replace("exec /usr/local/searxng/entrypoint.sh", f'cat "${{SEARXNG_SETTINGS_PATH:-{source}}}"')
    )
    (tmp_path / "start.sh").write_text(script)
    env = {k: v for k, v in os.environ.items() if k != "SEARCH_PROXIES"}
    if proxies is not None:
        env["SEARCH_PROXIES"] = proxies
    out = subprocess.run(["sh", str(tmp_path / "start.sh")], env=env, capture_output=True, text=True, check=True)
    return yaml.safe_load(out.stdout.split("\n", 1)[1] if proxies else out.stdout)


def test_without_proxies_settings_are_unchanged(tmp_path):
    settings = run_start(tmp_path, None)
    assert "proxies" not in settings["outgoing"]


def test_proxies_are_added_to_outgoing(tmp_path):
    settings = run_start(tmp_path, " http://user:it's@p1:8080 , ,socks5h://p2:1080")
    assert settings["outgoing"]["proxies"] == {"all://": ["http://user:it's@p1:8080", "socks5h://p2:1080"]}
    assert settings["outgoing"]["extra_proxy_timeout"] == 1
    # The rest of the settings are kept.
    assert settings["outgoing"]["request_timeout"] == 2.0
    assert any(e["name"] == "brave" for e in settings["engines"])
