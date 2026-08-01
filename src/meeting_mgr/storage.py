import functools
import shutil

import boto3
from botocore.exceptions import ClientError

from meeting_mgr.config import get_settings


@functools.lru_cache(maxsize=1)
def _get_client():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
    )


def _client():
    # lru_cache's internal lock makes the cache-miss build itself safe under
    # concurrent Celery workers. boto3 clients are thread-safe for requests;
    # it's the Session used to build one that isn't, so build it once.
    return _get_client()


def reset_client_cache() -> None:
    """Drop the cached S3 client so the next call rebuilds it from current
    settings. Only needed by tests that change storage settings mid-run --
    production never needs this since settings don't change post-boot."""
    _get_client.cache_clear()


_MISSING = {"404", "NoSuchBucket", "NotFound"}


def ensure_bucket() -> None:
    b = get_settings().s3_bucket
    try:
        _client().head_bucket(Bucket=b)
    except ClientError as e:
        # Only "bucket is missing" justifies creating it. A 403 means bad
        # credentials and a connection error means the endpoint is wrong —
        # both must surface as themselves, not as a create_bucket failure.
        if e.response.get("Error", {}).get("Code") not in _MISSING:
            raise
        _client().create_bucket(Bucket=b)


def put_object(key: str, data: bytes) -> None:
    _client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=data)


def put_stream(key: str, fileobj) -> None:
    """Stream a file-like object to storage without loading it into memory.

    Recordings are routinely hundreds of MB; upload_fileobj chunks and
    multiparts automatically, so peak memory is a buffer, not the whole file.
    """
    _client().upload_fileobj(fileobj, get_settings().s3_bucket, key)


def get_object(key: str) -> bytes:
    r = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    return r["Body"].read()


def get_stream(key: str, fileobj) -> None:
    """Stream storage into a file-like object without buffering it all."""
    _client().download_fileobj(get_settings().s3_bucket, key, fileobj)


def append_stream(key: str, fileobj) -> None:
    """Stream an object onto fileobj at its CURRENT position, without
    resetting it -- for writing multiple objects into one handle in sequence
    (chunk-manifest reconstruction).

    get_stream()/download_fileobj is NOT safe for this: s3transfer manages
    its own internal write cursor per transfer and writes from offset 0 on
    every call regardless of object size, so a second get_stream() into the
    same handle silently overwrites the first instead of appending -- this
    is not only an issue above the multipart threshold, it reproduces on
    two small chunks. Streaming the raw response body through
    shutil.copyfileobj instead uses plain sequential fh.write() calls, which
    respects wherever the handle already is.
    """
    body = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"]
    shutil.copyfileobj(body, fileobj)


def delete_object(key: str) -> None:
    _client().delete_object(Bucket=get_settings().s3_bucket, Key=key)


class RangeNotSatisfiable(Exception):
    """The requested range starts past the end of the object."""


def list_keys(prefix: str) -> list[str]:
    """List object keys under a prefix -- keys only, never bodies. Exists so
    capture-chunk resumability (GET /meetings/{id}/capture/chunks) never
    needs to download a chunk merely to know it exists."""
    paginator = _client().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=get_settings().s3_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def open_object(key: str, byte_range: str | None = None):
    """Open an object for streaming, optionally a byte range.

    Returns (stream, content_range, content_length) from a SINGLE request.
    S3 clamps an over-long end and reports the object's total size back in
    Content-Range, so there is no need to HEAD the object first, and no need
    to read it into memory to serve it.
    """
    kwargs = {"Range": byte_range} if byte_range else {}
    try:
        r = _client().get_object(Bucket=get_settings().s3_bucket, Key=key, **kwargs)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in {"InvalidRange", "416"}:
            raise RangeNotSatisfiable(byte_range) from e
        raise
    return r["Body"], r.get("ContentRange"), r["ContentLength"]
