"""Test the backend nudge the agent sends after saving results or news."""
from unittest.mock import MagicMock

import httpx

from ufc_agent.backend import notify_backend


def test_skips_without_configuration(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    client = MagicMock()
    assert "skipped" in notify_backend("news", client=client)
    client.post.assert_not_called()


def test_posts_with_token_and_returns_counts(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "https://api.example.com/")
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    client = MagicMock()
    client.post.return_value = httpx.Response(200, json={"news": 2},
                                              request=httpx.Request("POST", "https://api.example.com/internal/notify"))
    assert notify_backend("news", client=client) == {"news": 2}
    url = client.post.call_args.args[0]
    assert url == "https://api.example.com/internal/notify"
    assert client.post.call_args.kwargs["headers"]["X-Internal-Token"] == "s3cret"


def test_never_raises(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "https://api.example.com")
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    client = MagicMock()
    client.post.side_effect = httpx.ConnectTimeout("slow")
    assert "error" in notify_backend("live results", client=client)
