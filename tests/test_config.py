import os

from itr import config


def test_load_env_parses_comments_quotes_and_blank_values(tmp_path, monkeypatch):
    for k in ("A_KEY", "B_KEY", "C_KEY", "EMPTY_KEY"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text("# comment\nA_KEY=abc\nB_KEY = \"quoted value\"\n\nC_KEY='x'\nEMPTY_KEY=\nnot a pair\n", encoding="utf-8")
    names = config.load_env(f)
    assert os.environ["A_KEY"] == "abc" and os.environ["B_KEY"] == "quoted value" and os.environ["C_KEY"] == "x"
    assert "EMPTY_KEY" not in os.environ and sorted(names) == ["A_KEY", "B_KEY", "C_KEY"]


def test_real_environment_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("D_KEY", "from-env")
    f = tmp_path / ".env"
    f.write_text("D_KEY=from-file\n", encoding="utf-8")
    config.load_env(f)
    assert os.environ["D_KEY"] == "from-env"


def test_missing_file_is_fine(tmp_path):
    assert config.load_env(tmp_path / "nope.env") == []


def test_mask_never_reveals_full_secret():
    secret = "sk-abcdefghijklmnop"
    assert secret not in config.mask(secret) and config.mask(None) == "(not set)"
    assert "short" in config.mask("abc")
