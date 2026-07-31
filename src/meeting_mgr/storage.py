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

def ensure_bucket() -> None:
    b = get_settings().s3_bucket
    try:
        _client().head_bucket(Bucket=b)
    except ClientError:
        _client().create_bucket(Bucket=b)

def put_object(key: str, data: bytes) -> None:
    _client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=data)

def get_object(key: str) -> bytes:
    r = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    return r["Body"].read()

def delete_object(key: str) -> None:
    _client().delete_object(Bucket=get_settings().s3_bucket, Key=key)
