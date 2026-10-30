import pytest
from pydantic import ValidationError

from openscour.server.config import Settings


def test_search_proxies_are_split_and_trimmed():
    s = Settings(_env_file=None, search_proxies=" http://user:pw@p1:8080 ,, socks5h://p2:1080 ")
    assert s.search_proxy_list == ["http://user:pw@p1:8080", "socks5h://p2:1080"]
    assert Settings(_env_file=None).search_proxy_list == []


@pytest.mark.parametrize("bad", ["p1:8080", "ftp://p1:21", "http://"])
def test_invalid_search_proxy_is_rejected_without_echoing_it(bad):
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, search_proxies=f"http://user:secret@ok:8080,{bad}")
    assert "secret" not in str(e.value)
