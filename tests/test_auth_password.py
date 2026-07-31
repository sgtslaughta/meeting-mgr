from meeting_mgr.auth.password import hash_password, verify_password


def test_correct_password_verifies():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)


def test_wrong_password_does_not_verify():
    stored = hash_password("correct horse battery staple")
    assert not verify_password("wrong", stored)


def test_hash_is_salted_differently_each_time():
    a, b = hash_password("same"), hash_password("same")
    assert a != b, "identical passwords must not produce identical stored hashes"
    assert verify_password("same", a) and verify_password("same", b)


def test_none_stored_hash_never_verifies():
    # Kill: hardcoding `return True` for a falsy stored value, or comparing
    # None with `==` instead of rejecting it outright, both pass without this.
    assert not verify_password("anything", None)


def test_malformed_stored_hash_does_not_verify():
    # Kill: dropping the `sep`/hex-decode guards so a malformed stored value
    # raises instead of failing closed.
    assert not verify_password("anything", "not-a-valid-stored-hash")
