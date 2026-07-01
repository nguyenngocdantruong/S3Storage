import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from domain.ports.storage import StorageProvider


class Boto3StorageProvider(StorageProvider):
    def __init__(self, connection, endpoint_url=None):
        config = Config(
            signature_version='s3v4',
            retries={'max_attempts': 3},
            s3={'addressing_style': 'path'},
        )
        endpoint = endpoint_url if endpoint_url is not None else connection.endpoint_url
        endpoint = endpoint if (endpoint and endpoint.strip()) else None
        self._client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=connection.access_key,
            aws_secret_access_key=connection.secret_key,
            region_name=connection.region_name or 'us-east-1',
            config=config,
        )

    def __getattr__(self, name):
        return getattr(self._client, name)

    def list_buckets(self):
        return self._client.list_buckets()

    def head_object(self, bucket: str, key: str):
        return self._client.head_object(Bucket=bucket, Key=key)

    def list_objects(self, bucket: str, prefix: str = ''):
        return self._client.list_objects_v2(Bucket=bucket, Prefix=prefix)

    def copy_object(self, source_bucket: str, source_key: str, dest_bucket: str, dest_key: str):
        return self._client.copy_object(
            CopySource={'Bucket': source_bucket, 'Key': source_key},
            Bucket=dest_bucket,
            Key=dest_key,
        )

    def delete_object(self, bucket: str, key: str):
        return self._client.delete_object(Bucket=bucket, Key=key)

    def generate_presigned_url(self, operation: str, *args, params: dict | None = None, expires_in: int = 3600, **kwargs):
        if params is not None and 'Params' not in kwargs:
            kwargs['Params'] = params
        if 'ExpiresIn' not in kwargs:
            kwargs['ExpiresIn'] = expires_in
        return self._client.generate_presigned_url(operation, *args, **kwargs)


def fix_url(url, is_https):
    if not url:
        return url
    if is_https and url.startswith('http://'):
        return url.replace('http://', 'https://', 1)
    return url


def s3_key_exists(s3_client, bucket, key):
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def get_storage_provider(connection, endpoint_url=None):
    return Boto3StorageProvider(connection, endpoint_url=endpoint_url)
