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


def _http(comments):
    def fake_get(url, token=None):
        if url.endswith("/issues/5"):
            return {"title": "T", "body": "B"}
        if "/comments" in url:
            return comments
        return {"default_branch": "develop"}
    return fake_get


CS = [{"user": {"login": "a"}, "body": "early", "created_at": "2020-01-01T10:00:00Z"},
      {"user": {"login": "b"}, "body": "fix is X", "created_at": "2020-03-01T10:00:00Z"},
      {"user": {"login": "c"}, "body": "no stamp"}]


def test_as_of_drops_comments_written_after_the_cutoff():
    out = fetch_issue("https://github.com/o/r/issues/5", http_get=_http(CS), as_of="2020-02-01T00:00:00+00:00")
    assert [c["body"] for c in out["comments"]] == ["early", "no stamp"]


def test_as_of_compares_instants_across_time_zones():
    # 2020-01-01T15:00+05:30 is 09:30Z, i.e. BEFORE the 10:00Z comment
    out = fetch_issue("https://github.com/o/r/issues/5", http_get=_http(CS), as_of="2020-01-01T15:00:00+05:30")
    assert [c["body"] for c in out["comments"]] == ["no stamp"]


def test_no_as_of_keeps_everything():
    out = fetch_issue("https://github.com/o/r/issues/5", http_get=_http(CS))
    assert len(out["comments"]) == 3


def test_fetch_stage_does_not_let_the_default_branch_overwrite_a_pinned_ref(monkeypatch):
    from itr import github
    from itr.agent.stages.fetch import fetch
    from tests.fakes import make_ctx
    seen = {}

    def fake_fetch(url, token=None, http_get=None, as_of=None):
        seen["as_of"] = as_of
        return {"url": url, "repo": "o/r", "number": 5, "title": "T", "body": "B", "comments": [], "ref": "develop"}
    monkeypatch.setattr(github, "fetch_issue", fake_fetch)
    ctx, _ = make_ctx(issue={"url": "https://github.com/o/r/issues/5", "ref": "abc123", "ref_pinned": True,
                             "as_of": "2020-02-01T00:00:00+00:00"})
    fetch(ctx)
    assert ctx.run.issue["ref"] == "abc123" and ctx.run.issue["ref_pinned"] is True
    assert ctx.run.issue["title"] == "T" and seen["as_of"] == "2020-02-01T00:00:00+00:00"
