from security import SlidingWindow, hash_password, verify_password


def test_hash_roundtrip():
    stored = hash_password("correct horse")
    assert stored != "correct horse"
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_plain_text_legacy_value_still_works():
    assert verify_password("old-password", "old-password")
    assert not verify_password("nope", "old-password")


def test_empty_or_garbage_stored_value_never_matches():
    assert not verify_password("", "")
    assert not verify_password("x", "pbkdf2_sha256$not$valid")


def test_sliding_window_blocks_then_recovers(monkeypatch):
    import security
    now = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
    w = SlidingWindow(limit=2, window=60)
    assert w.allow("a") and w.allow("a")
    assert not w.allow("a")
    assert w.allow("b")            # other keys are independent
    now[0] += 61
    assert w.allow("a")            # window has passed


def test_supabase_url_is_normalised():
    import db
    base = "https://abc.supabase.co"
    for messy in [base, base + "/", base + "/rest/v1", base + "/rest/v1/", "  " + base + "  "]:
        assert db._clean_supabase_url(messy) == base
    assert db._clean_supabase_url(None) == ""


def test_admin_password_lookup_handles_empty_and_filled_settings(monkeypatch):
    from types import SimpleNamespace
    import db

    class FakeQuery:
        def __init__(self, rows): self.rows = rows
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return SimpleNamespace(data=self.rows)

    class FakeClient:
        def __init__(self, rows): self.rows = rows
        def table(self, name): return FakeQuery(self.rows)

    monkeypatch.setattr(db, "USE_SUPABASE", True)
    monkeypatch.setenv("ADMIN_PASSWORD", "from-env")

    monkeypatch.setattr(db, "supabase", FakeClient([]), raising=False)
    assert db.get_admin_password() == "from-env"            # empty table -> falls back to .env

    monkeypatch.setattr(db, "supabase", FakeClient([{"value": "stored-hash"}]), raising=False)
    assert db.get_admin_password() == "stored-hash"         # saved password wins
