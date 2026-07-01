import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import g

from application import create_app
from interfaces.api import views as api_views


class ApiAuditActionTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({'TESTING': True})

    def _run_paste(self, action):
        dest_conn = SimpleNamespace(id=10, name='dest-conn')
        src_conn = SimpleNamespace(id=20, name='src-conn')
        fake_query_result = MagicMock()
        fake_query_result.first_or_404.return_value = dest_conn
        fake_query_result.first.return_value = src_conn
        fake_connection_model = SimpleNamespace(query=MagicMock(filter_by=MagicMock(side_effect=[fake_query_result, fake_query_result])))
        dest_s3 = MagicMock()

        with self.app.test_request_context('/api/paste', method='POST', json={
            'dest_connection_id': 'dest-id',
            'dest_bucket_name': 'bucket-a',
            'dest_prefix': '',
            'action': action,
            'items': [
                {
                    'connection_id': 'src-id',
                    'bucket_name': 'bucket-a',
                    'key': 'folder/report.pdf',
                    'type': 'file',
                    'name': 'report.pdf',
                }
            ],
            'resolutions': {},
        }):
            g.user = SimpleNamespace(id=7, role='User')
            with patch.object(api_views, 'S3Connection', fake_connection_model), \
                 patch.object(api_views, 'access_control_check_bucket_edit_access', return_value=True), \
                 patch.object(api_views, 'access_control_check_file_edit_access', return_value=True), \
                 patch.object(api_views, '_get_s3_client', return_value=dest_s3), \
                 patch.object(api_views, '_paste_single_file'), \
                 patch.object(api_views, '_log_action') as log_action, \
                 patch.object(api_views.db, 'session', MagicMock()):
                response = api_views.paste_selected_items()
        return response, log_action

    def test_paste_selected_items_logs_copy_file_action(self):
        response, log_action = self._run_paste('copy')
        self.assertEqual(response.json['status'], 'success')
        self.assertEqual(log_action.call_args[0][4], 'COPY_FILE')

    def test_paste_selected_items_logs_move_file_action(self):
        response, log_action = self._run_paste('move')
        self.assertEqual(response.json['status'], 'success')
        self.assertEqual(log_action.call_args[0][4], 'MOVE_FILE')


if __name__ == '__main__':
    unittest.main()
