"""Release-candidate regression coverage for dynamic docs and optional portal sources."""

from __future__ import annotations

from gc import isenabled
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import mark
from statline import __version__
from statline.gateway.http.app import app

ROOT = Path(__file__).resolve().parents[1]


def test_rc7_keeps_python_garbage_collection_disabled() -> None:
    # The import-time contract intentionally stays in rc7; custom GC belongs to rc8.
    assert __version__.startswith("4.0.0rc7")
    assert not isenabled()


def test_rtfm_serves_branded_live_openapi_reference() -> None:
    with TestClient(app) as client:
        response = client.get("/rtfm")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "StatLine" in response.text
    assert 'fetch("/openapi.json"' in response.text
    assert "https://statline.dev/dashboard" in response.text
    assert "<iframe" not in response.text


@mark.skipif(
    not (ROOT / "portal").is_dir(),
    reason="Portal is separately built and excluded from the Python source distribution",
)
def test_portal_has_live_history_rankings_and_crawl_policy() -> None:
    portal = ROOT / "portal"
    assert "User-agent: *" in (portal / "public/robots.txt").read_text()
    assert "Disallow: /dashboard" in (portal / "public/robots.txt").read_text()
    assert "statline.dev/sitemap.xml" in (portal / "public/robots.txt").read_text()
    assert '"/changelog"' in (portal / "app/sitemap.ts").read_text()
    changelog = (portal / "components/statline/changelog.tsx").read_text()
    assert '"/releases"' in changelog and '"/tags"' in changelog
    assert "/compare/" in changelog
    assert "ReleaseNotes source={entry.release.body}" in changelog
    markdown = (portal / "components/statline/release-markdown.tsx").read_text()
    assert "lexer(source, { gfm: true, breaks: true })" in markdown
    assert "dangerouslySetInnerHTML" not in markdown
    assert "safeLink(" in markdown
    rank = (portal / "lib/rank-results.ts").read_text()
    assert "row.pri" in rank
    for target in ("live-score.tsx", "playground.tsx"):
        assert "rankedResults" in (portal / "components/statline" / target).read_text()
