from meeting_mgr.storage import ensure_bucket, put_object, get_object, delete_object

def test_roundtrip():
    ensure_bucket()
    put_object("t/x.wav", b"hello")
    assert get_object("t/x.wav") == b"hello"
    delete_object("t/x.wav")
