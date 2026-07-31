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
    with pytest.raises(ClientError):
        storage.ensure_bucket()
