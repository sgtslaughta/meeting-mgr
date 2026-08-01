import pytest
from botocore.exceptions import ClientError

from meeting_mgr import storage
from meeting_mgr.storage import delete_object, ensure_bucket, get_object, put_object


def test_roundtrip():
    ensure_bucket()
    put_object("t/x.wav", b"hello")
    assert get_object("t/x.wav") == b"hello"
    delete_object("t/x.wav")


def test_ensure_bucket_reraises_non_404(monkeypatch):
    class Boom:
        def head_bucket(self, **kw):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket")

        def create_bucket(self, **kw):
            raise AssertionError("must not create a bucket on a 403")

    monkeypatch.setattr(storage, "_client", lambda: Boom())
    # Other tests (this one may run after them) have already ensured this
    # same bucket name successfully, which the "already ensured" cache would
    # otherwise short-circuit on -- never calling head_bucket at all, and
    # silently passing this test for the wrong reason.
    storage.reset_bucket_cache()
    with pytest.raises(ClientError):
        storage.ensure_bucket()


def test_client_is_cached():
    # If _client() builds a fresh boto3 client every call, this identity
    # check fails -- that's the line (`return _get_client()`) the mutation
    # targets: swap it back to building inline and `is` becomes False.
    storage.reset_client_cache()
    try:
        assert storage._client() is storage._client()
    finally:
        storage.reset_client_cache()


def test_ensure_bucket_head_bucket_called_once(monkeypatch):
    calls = {"head_bucket": 0}

    class Counting:
        def head_bucket(self, **kw):
            calls["head_bucket"] += 1

    monkeypatch.setattr(storage, "_client", lambda: Counting())
    storage.reset_bucket_cache()
    storage.ensure_bucket()
    storage.ensure_bucket()
    # Deleting the "already ensured" short-circuit (the `if b in
    # _ensured_buckets: return` line) makes this 2, not 1.
    assert calls["head_bucket"] == 1
