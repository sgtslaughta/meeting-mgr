import boto3
from botocore.exceptions import ClientError
from meeting_mgr.config import get_settings

def _client():
    s = get_settings()
    return boto3.client(
        "s3", endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
    )

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

def delete_object(key: str) -> None:
    _client().delete_object(Bucket=get_settings().s3_bucket, Key=key)
