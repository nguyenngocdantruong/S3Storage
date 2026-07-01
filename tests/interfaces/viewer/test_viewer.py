import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from interfaces.viewer.views import bp as viewer_bp


class FakeConnQuery:
    def __init__(self, conn):
        self.conn = conn

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.conn


class FakeBucketQuery:
    def __init__(self, mapping):
        self.mapping = mapping

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.mapping


class FakeProgressQuery:
    def __init__(self, progress=None):
        self.progress = progress

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.progress


class FakeLikeQuery:
    def __init__(self, like=None):
        self.like = like

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.like


class ViewerRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.app.register_blueprint(viewer_bp)
        self.app.config['GET_S3_CLIENT'] = MagicMock()
        self.app.config['FIX_S3_URL'] = lambda url: url

    def test_view_file_uses_presigned_url_without_proxy_for_regular_video(self):
        conn = SimpleNamespace(id=1, name='conn', endpoint_url='https://s3.example.com', upload_endpoint=None, connection_id='conn-1')
        mapping = SimpleNamespace(access_type='public', user_id=1)
        progress = None
        like = None
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = 'https://signed.example/file'
        self.app.config['GET_S3_CLIENT'].return_value = s3

        with self.app.test_request_context('/connection/conn-1/bucket/demo/viewer?key=movie.mp4'):
            with patch('interfaces.viewer.views.S3Connection.query', FakeConnQuery(conn)), \
                 patch('interfaces.viewer.views.UserBucket.query', FakeBucketQuery(mapping)), \
                 patch('interfaces.viewer.views.VideoProgress.query', FakeProgressQuery(progress)), \
                 patch('interfaces.viewer.views.ItemLike.query', FakeLikeQuery(like)), \
                 patch('interfaces.viewer.views.access_control_check_bucket_access', return_value=True), \
                 patch('interfaces.viewer.views.access_control_check_bucket_edit_access', return_value=True), \
                 patch('interfaces.viewer.views.render_template', side_effect=lambda template, **ctx: ctx), \
                 patch('interfaces.viewer.views.g', SimpleNamespace(user=None)):
                from interfaces.viewer.views import view_file
                result = view_file('conn-1', 'demo')

        self.assertEqual(result['presigned_url'], 'https://signed.example/file')
        s3.generate_presigned_url.assert_called_once_with('get_object', Params={'Bucket': 'demo', 'Key': 'movie.mp4'}, ExpiresIn=3600)

    def test_view_file_uses_proxy_for_text_file(self):
        conn = SimpleNamespace(id=1, name='conn', endpoint_url='https://s3.example.com', upload_endpoint=None, connection_id='conn-1')
        mapping = SimpleNamespace(access_type='public', user_id=1)
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = 'https://signed.example/file'
        self.app.config['GET_S3_CLIENT'].return_value = s3

        with self.app.test_request_context('/connection/conn-1/bucket/demo/viewer?key=readme.txt'):
            with patch('interfaces.viewer.views.S3Connection.query', FakeConnQuery(conn)), \
                 patch('interfaces.viewer.views.UserBucket.query', FakeBucketQuery(mapping)), \
                 patch('interfaces.viewer.views.VideoProgress.query', FakeProgressQuery()), \
                 patch('interfaces.viewer.views.ItemLike.query', FakeLikeQuery()), \
                 patch('interfaces.viewer.views.access_control_check_bucket_access', return_value=True), \
                 patch('interfaces.viewer.views.access_control_check_bucket_edit_access', return_value=False), \
                 patch('interfaces.viewer.views.render_template', side_effect=lambda template, **ctx: ctx), \
                 patch('interfaces.viewer.views.g', SimpleNamespace(user=None)):
                from interfaces.viewer.views import view_file
                result = view_file('conn-1', 'demo')

        self.assertIn('/proxy-file', result['presigned_url'])


if __name__ == '__main__':
    unittest.main()
