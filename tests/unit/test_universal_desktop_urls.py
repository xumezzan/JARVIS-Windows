"""Literal navigation policy has no site-name recipes and blocks local address forms."""

import pytest

from jarvis.tools.base import ToolError
from jarvis.tools.windows import chrome_url, normalize_chrome_url


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST.",
        "test.localhost",
        "127.0.0.1",
        "127.0.0.1.",
        "2130706433.",
        "127%2e0%2e0%2e1",
        "127.1",
        "2130706433",
        "0x7f000001",
        "0177.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "[::1]",
        "[::ffff:127.0.0.1]",
    ],
)
def test_local_literals_are_rejected_before_execution(host: str) -> None:
    with pytest.raises(ToolError, match="network_denied"):
        chrome_url("http://" + host + "/")


@pytest.mark.parametrize("host", ["example.com", "arbitrary-app.example", "intranet", "instagram"])
def test_unknown_hosts_are_literal_navigation_not_curated_recipes(host: str) -> None:
    assert normalize_chrome_url(host) == "https://" + host + "/"
