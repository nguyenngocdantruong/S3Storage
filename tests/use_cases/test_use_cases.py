import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from use_cases import access_control, audit, file_ops, file_type, quota, slug


class FakeQueryResult:
    def __init__(self, first_result=None, all_result=None):
        self._first_result = first_result
        self._all_result = all_result if all_result is not None else []
        self.deleted = []
        self.updated = []

    def first(self):
        return self._first_result

    def all(self):
        return self._all_result

    def delete(self, synchronize_session=False):
        self.deleted.append(synchronize_session)

    def update(self, values, synchronize_session=False):
        self.updated.append((values, synchronize_session))


class FakeQuery:
    def __init__(self, first_result=None, all_result=None):
        self._first_result = first_result
        self._all_result = all_result if all_result is not None else []

    def filter_by(self, **kwargs):
        return FakeQueryResult(first_result=self._first_result, all_result=self._all_result)

    def filter(self, *args, **kwargs):
        return FakeQueryResult(first_result=self._first_result, all_result=self._all_result)


class FakeUploadedFileModel:
    connection_id = object()
    bucket_name = object()
    file_key = object()
    user_id = object()
    query = FakeQuery()

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeUserBucketModel:
    query = FakeQuery()


class FakeBucketAccessModel:
    query = FakeQuery()


class FakeS3ConnectionModel:
    pass


class FakeVideoProgressModel:
    connection_name = 'connection_name'
    bucket_name = 'bucket_name'
    file_key = 'file_key'
    file_name = 'file_name'
    query = FakeQuery()


class FakeVideoNoteModel:
    connection_name = 'connection_name'
    bucket_name = 'bucket_name'
    file_key = 'file_key'
    query = FakeQuery()


class FakeItemLikeModel:
    connection_name = 'connection_name'
    bucket_name = 'bucket_name'
    file_key = 'file_key'
    query = FakeQuery()


class AccessControlTests(unittest.TestCase):
    def test_bucket_access_allows_public_bucket_without_user(self):
        fake_user_bucket = SimpleNamespace(query=FakeQuery(first_result=SimpleNamespace(access_type='public', user_id=99)))
        fake_bucket_access = SimpleNamespace(query=FakeQuery(first_result=None))
        with patch.object(access_control, 'UserBucket', fake_user_bucket), \
             patch.object(access_control, 'BucketAccess', fake_bucket_access):
            allowed = access_control.check_bucket_access(None, SimpleNamespace(id=1), 'bucket')
        self.assertTrue(allowed)

    def test_bucket_edit_access_requires_editor_when_shared(self):
        mapping = SimpleNamespace(user_id=2, access_type='restricted')
        shared = SimpleNamespace(role='Viewer')
        user = SimpleNamespace(id=3, role='User')
        with patch.object(access_control, 'UserBucket', SimpleNamespace(query=FakeQuery(first_result=mapping))), \
             patch.object(access_control, 'BucketAccess', SimpleNamespace(query=FakeQuery(first_result=shared))):
            allowed = access_control.check_bucket_edit_access(user, SimpleNamespace(id=1), 'bucket')
        self.assertFalse(allowed)

    def test_file_edit_access_allows_public_upload_for_owned_file(self):
        mapping = SimpleNamespace(user_id=2, access_type='public_upload')
        uploaded_file = SimpleNamespace(user_id=5)
        user = SimpleNamespace(id=5, role='User')
        fake_uploaded_file = type('UploadedFile', (), {
            'connection_id': object(),
            'bucket_name': object(),
            'file_key': type('FileKeyField', (), {'startswith': lambda self, value: ('startswith', value)})(),
            'user_id': object(),
            'query': FakeQuery(first_result=uploaded_file),
        })
        with patch.object(access_control, 'UserBucket', SimpleNamespace(query=FakeQuery(first_result=mapping))), \
             patch.object(access_control, 'BucketAccess', SimpleNamespace(query=FakeQuery(first_result=None))), \
             patch.object(access_control, 'UploadedFile', fake_uploaded_file):
            allowed = access_control.check_file_edit_access(user, SimpleNamespace(id=1), 'bucket', 'file.txt')
        self.assertTrue(allowed)

    def test_file_edit_access_denies_folder_when_other_user_uploaded_inside(self):
        mapping = SimpleNamespace(user_id=2, access_type='public_upload')
        uploaded_by_other = SimpleNamespace(user_id=9)
        user = SimpleNamespace(id=5, role='User')
        fake_uploaded_file = type('UploadedFile', (), {
            'connection_id': object(),
            'bucket_name': object(),
            'file_key': type('FileKeyField', (), {'startswith': lambda self, value: ('startswith', value)})(),
            'user_id': object(),
            'query': FakeQuery(first_result=uploaded_by_other),
        })
        with patch.object(access_control, 'UserBucket', SimpleNamespace(query=FakeQuery(first_result=mapping))), \
             patch.object(access_control, 'BucketAccess', SimpleNamespace(query=FakeQuery(first_result=None))), \
             patch.object(access_control, 'UploadedFile', fake_uploaded_file):
            allowed = access_control.check_file_edit_access(user, SimpleNamespace(id=1), 'bucket', 'folder/')
        self.assertFalse(allowed)


class AuditTests(unittest.TestCase):
    def test_log_action_adds_and_commits(self):
        session = MagicMock()
        audit.log_action(1, 2, 'conn', 'bucket', 'CREATE', 'details', db_session=session)
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_log_action_swallows_exceptions(self):
        session = MagicMock()
        session.add.side_effect = RuntimeError('db failed')
        audit.log_action(1, 2, 'conn', 'bucket', 'CREATE', 'details', db_session=session)
        session.commit.assert_not_called()


class QuotaTests(unittest.TestCase):
    def test_get_user_storage_used_sums_sizes_and_caches_client(self):
        user = SimpleNamespace(id=10)
        bucket_rows = [SimpleNamespace(connection_id=1, bucket_name='a'), SimpleNamespace(connection_id=1, bucket_name='b')]
        paginator = MagicMock()
        paginator.paginate.side_effect = [
            [{'Contents': [{'Size': 5}, {'Size': 7}]}],
            [{'Contents': [{'Size': 3}]}],
        ]
        client = MagicMock()
        client.get_paginator.return_value = paginator
        session = MagicMock()
        session.get.return_value = SimpleNamespace(id=1)

        with patch.object(quota, 'UserBucket', SimpleNamespace(query=FakeQuery(all_result=bucket_rows))), \
             patch.object(quota, 'S3Connection', FakeS3ConnectionModel):
            total = quota.get_user_storage_used(user, db_session=session, storage_provider_factory=MagicMock(return_value=client))

        self.assertEqual(total, 15)
        client.get_paginator.assert_called_with('list_objects_v2')

    def test_get_user_storage_used_ignores_connection_errors(self):
        user = SimpleNamespace(id=10)
        bucket_rows = [SimpleNamespace(connection_id=1, bucket_name='a')]
        session = MagicMock()
        session.get.return_value = SimpleNamespace(id=1)
        with patch.object(quota, 'UserBucket', SimpleNamespace(query=FakeQuery(all_result=bucket_rows))), \
             patch.object(quota, 'S3Connection', FakeS3ConnectionModel):
            total = quota.get_user_storage_used(user, db_session=session, storage_provider_factory=MagicMock(side_effect=RuntimeError('offline')))
        self.assertEqual(total, 0)


class FileOpsTests(unittest.TestCase):
    def setUp(self):
        self.src_conn = SimpleNamespace(id=1, name='src')
        self.dest_conn = SimpleNamespace(id=2, name='dest')
        self.src_s3 = MagicMock()
        self.dest_s3 = MagicMock()
        self.session = MagicMock()

    def _patch_models(self, uploaded_first=None):
        fake_uploaded = type('UploadedFile', (), {
            'query': FakeQuery(first_result=uploaded_first),
            '__init__': lambda self, **kwargs: self.__dict__.update(kwargs),
        })
        fake_progress = type('VideoProgress', (), {
            'connection_name': 'connection_name',
            'bucket_name': 'bucket_name',
            'file_key': 'file_key',
            'file_name': 'file_name',
            'query': FakeQuery(),
        })
        fake_note = type('VideoNote', (), {
            'connection_name': 'connection_name',
            'bucket_name': 'bucket_name',
            'file_key': 'file_key',
            'query': FakeQuery(),
        })
        fake_like = type('ItemLike', (), {
            'connection_name': 'connection_name',
            'bucket_name': 'bucket_name',
            'file_key': 'file_key',
            'query': FakeQuery(),
        })
        return patch.multiple(file_ops, UploadedFile=fake_uploaded, VideoProgress=fake_progress, VideoNote=fake_note, ItemLike=fake_like)

    def test_paste_single_file_returns_early_for_same_target(self):
        factory = MagicMock(side_effect=[self.src_s3, self.dest_s3])
        file_ops.paste_single_file(self.src_conn, 'bucket', 'file.txt', self.src_conn, 'bucket', 'file.txt', 'copy', current_user_id=1, db_session=self.session, storage_provider_factory=factory)
        self.src_s3.copy_object.assert_not_called()
        self.dest_s3.copy_object.assert_not_called()

    def test_paste_single_file_cross_connection_copy_uploads_stream(self):
        self.src_s3.get_object.return_value = {'Body': b'data', 'ContentType': 'text/plain'}
        factory = MagicMock(side_effect=[self.src_s3, self.dest_s3])
        with self._patch_models(uploaded_first=SimpleNamespace(user_id=99)):
            file_ops.paste_single_file(self.src_conn, 'srcb', 'file.txt', self.dest_conn, 'destb', 'new.txt', 'copy', current_user_id=1, db_session=self.session, storage_provider_factory=factory)
        self.dest_s3.upload_fileobj.assert_called_once()
        self.session.add.assert_called_once()

    def test_paste_single_file_move_updates_existing_uploaded_file(self):
        existing = SimpleNamespace(connection_id=1, bucket_name='srcb', file_key='file.txt')
        factory = MagicMock(side_effect=[self.src_s3, self.dest_s3])
        with self._patch_models(uploaded_first=existing):
            file_ops.paste_single_file(self.src_conn, 'srcb', 'file.txt', self.dest_conn, 'destb', 'new.txt', 'move', current_user_id=1, db_session=self.session, storage_provider_factory=factory)
        self.src_s3.delete_object.assert_called_once()
        self.assertEqual(existing.connection_id, 2)
        self.assertEqual(existing.bucket_name, 'destb')
        self.assertEqual(existing.file_key, 'new.txt')


class FileTypeTests(unittest.TestCase):
    def test_classify_known_extensions(self):
        self.assertEqual(file_type.classify_file_type('mp4'), 'video')
        self.assertEqual(file_type.classify_file_type('pdf'), 'pdf')
        self.assertEqual(file_type.classify_file_type('docx'), 'docx')
        self.assertEqual(file_type.classify_file_type('png'), 'image')
        self.assertEqual(file_type.classify_file_type('txt'), 'text')

    def test_classify_unknown_extension(self):
        self.assertEqual(file_type.classify_file_type('bin'), 'unknown')


class SlugTests(unittest.TestCase):
    def test_generate_unique_slug_uses_requested_slug_and_suffixes(self):
        value = slug.generate_unique_slug('Ignored Name', {'custom', 'custom-1'}, requested_slug='custom')
        self.assertEqual(value, 'custom-2')

    def test_generate_unique_slug_falls_back_when_name_missing(self):
        with patch('use_cases.slug.os.urandom', return_value=b'\x01\x02\x03\x04'):
            value = slug.generate_unique_slug('', set())
        self.assertEqual(value, 'conn-01020304')


if __name__ == '__main__':
    unittest.main()
