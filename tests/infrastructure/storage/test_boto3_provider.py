import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from infrastructure.storage import boto3_provider


class StorageProviderTests(unittest.TestCase):
    def test_fix_url_upgrades_http_when_https_site(self):
        self.assertEqual(boto3_provider.fix_url('http://example.com/file', True), 'https://example.com/file')

    def test_fix_url_keeps_url_when_not_https(self):
        self.assertEqual(boto3_provider.fix_url('http://example.com/file', False), 'http://example.com/file')

    def test_s3_key_exists_returns_true_when_head_succeeds(self):
        client = MagicMock()
        self.assertTrue(boto3_provider.s3_key_exists(client, 'bucket', 'key'))

    def test_s3_key_exists_returns_false_on_client_error(self):
        client = MagicMock()
        client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        self.assertFalse(boto3_provider.s3_key_exists(client, 'bucket', 'key'))

    @patch('infrastructure.storage.boto3_provider.boto3.client')
    def test_get_storage_provider_builds_boto3_client(self, boto_client):
        boto_client.return_value = MagicMock()
        conn = SimpleNamespace(
            endpoint_url='http://s3.local',
            access_key='ak',
            secret_key='sk',
            region_name='us-east-1',
        )
        provider = boto3_provider.get_storage_provider(conn)
        self.assertIsInstance(provider, boto3_provider.Boto3StorageProvider)
        boto_client.assert_called_once()

    def test_generate_presigned_url_supports_boto3_style_keywords(self):
        conn = SimpleNamespace(endpoint_url='http://s3.local', access_key='ak', secret_key='sk', region_name='us-east-1')
        with patch('infrastructure.storage.boto3_provider.boto3.client', return_value=MagicMock()) as boto_client:
            provider = boto3_provider.get_storage_provider(conn)
            provider.generate_presigned_url('get_object', Params={'Bucket': 'b', 'Key': 'k'}, ExpiresIn=123)
        boto_client.return_value.generate_presigned_url.assert_called_once_with('get_object', Params={'Bucket': 'b', 'Key': 'k'}, ExpiresIn=123)

    def test_generate_presigned_url_supports_adapter_style_keywords(self):
        conn = SimpleNamespace(endpoint_url='http://s3.local', access_key='ak', secret_key='sk', region_name='us-east-1')
        with patch('infrastructure.storage.boto3_provider.boto3.client', return_value=MagicMock()) as boto_client:
            provider = boto3_provider.get_storage_provider(conn)
            provider.generate_presigned_url('get_object', params={'Bucket': 'b', 'Key': 'k'}, expires_in=321)
        boto_client.return_value.generate_presigned_url.assert_called_once_with('get_object', Params={'Bucket': 'b', 'Key': 'k'}, ExpiresIn=321)


if __name__ == '__main__':
    unittest.main()
