import uuid
from concurrent.futures import ThreadPoolExecutor

from meeting_mgr.storage import ensure_bucket, list_keys, put_object


def test_list_keys_returns_only_matching_prefix():
    # Kill: a helper that lists the whole bucket (ignores/defaults the
    # prefix) would return the "other-" key too; sorted(keys) would fail.
    ensure_bucket()
    prefix = f"test-list-{uuid.uuid4()}/"
    put_object(f"{prefix}a", b"1")
    put_object(f"{prefix}b", b"2")
    put_object(f"other-{uuid.uuid4()}/c", b"3")

    keys = list_keys(prefix)

    assert sorted(keys) == [f"{prefix}a", f"{prefix}b"]


def test_list_keys_on_an_empty_prefix_returns_an_empty_list():
    # Kill: a helper that raises (e.g. treats zero Contents as an error)
    # instead of returning [] would fail here.
    ensure_bucket()
    assert list_keys(f"nothing-here-{uuid.uuid4()}/") == []


def test_list_keys_paginates_past_the_1000_key_page_cap():
    # Kill: a helper built on a single list_objects_v2 call (no paginator)
    # is silently capped at 1000 keys by S3/MinIO -- this fixture writes
    # 1001 keys under the prefix, so a non-paginating implementation would
    # return len(keys) == 1000, not 1001.
    ensure_bucket()
    prefix = f"test-list-page-{uuid.uuid4()}/"
    count = 1001

    def upload(i: int) -> None:
        put_object(f"{prefix}{i:05d}", b"x")

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(upload, range(count)))

    keys = list_keys(prefix)

    assert len(keys) == count
    assert sorted(keys) == [f"{prefix}{i:05d}" for i in range(count)]
