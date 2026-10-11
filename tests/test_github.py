import pytest

from itr.github import IssueURLError, fetch_issue, parse_issue_url


def test_parse_issue_url():
    assert parse_issue_url("https://github.com/pydantic/pydantic/issues/123") == ("pydantic", "pydantic", 123)
    assert parse_issue_url("https://github.com/a/b.c/issues/7#issuecomment-1") == ("a", "b.c", 7)


@pytest.mark.parametrize("bad", ["https://github.com/a/b/pull/3", "https://example.com/a/b/issues/1", "nope"])
def test_parse_rejects_non_issue_urls(bad):
    with pytest.raises(IssueURLError):
        parse_issue_url(bad)


def test_fetch_issue_shapes_the_run_issue_dict():
    def fake_get(url, token=None):
        if url.endswith("/issues/5"):
            return {"title": "T", "body": None}
        if "/comments" in url:
            return [{"user": {"login": "bob"}, "body": "same here"}]
        return {"default_branch": "develop"}
    out = fetch_issue("https://github.com/o/r/issues/5", http_get=fake_get)
    assert out == {"url": "https://github.com/o/r/issues/5", "repo": "o/r", "number": 5, "title": "T",
                   "body": "", "comments": [{"author": "bob", "body": "same here"}], "ref": "develop"}


def test_fetch_stage_keeps_preset_issue_and_ref():
    from itr.agent.stages.fetch import fetch
    from tests.fakes import make_ctx
    ctx, _ = make_ctx(issue={"url": "u", "title": "T", "ref": "abc123"})
    fetch(ctx)                                                 # must not hit the network
    assert ctx.run.issue["ref"] == "abc123"
