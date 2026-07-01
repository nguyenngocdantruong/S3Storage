import unittest

from app import app
from infrastructure.persistence.models import BucketAccess, S3Connection, User, UserBucket, VideoProgress


class RouteSmokeAuditTests(unittest.TestCase):
    def test_all_routes_dispatch_without_server_errors(self):
        with app.app_context():
            conn = S3Connection.query.first()
            bucket_map = UserBucket.query.filter_by(connection_id=conn.id).first() if conn else None
            user = User.query.first()
            progress = VideoProgress.query.first()
            access = BucketAccess.query.first()

        sample = {
            'connection_id': getattr(conn, 'connection_id', 'sample-conn'),
            'bucket_name': getattr(bucket_map, 'bucket_name', 'sample-bucket'),
            'key': 'sample.txt',
            'progress_id': getattr(progress, 'id', 1),
            'user_id': getattr(user, 'id', 1),
            'access_id': getattr(access, 'id', 1),
            'page': 1,
        }

        client = app.test_client()
        results = []
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, sorted(r.methods))):
            if rule.endpoint == 'static':
                continue

            method = 'GET' if 'GET' in rule.methods else 'POST'
            values = {}
            for arg in rule.arguments:
                if arg in sample:
                    values[arg] = sample[arg]
                elif arg.endswith('_id'):
                    values[arg] = '1'
                else:
                    values[arg] = 'sample'

            if method == 'GET':
                resp = client.get(rule.rule, query_string=values, follow_redirects=False)
            else:
                data = values.copy()
                if rule.rule.startswith('/api/'):
                    resp = client.post(rule.rule, json=data, query_string=values, follow_redirects=False)
                else:
                    resp = client.post(rule.rule, data=data, query_string=values, follow_redirects=False)

            results.append((rule.endpoint, method, resp.status_code, rule.rule))

        bad = [row for row in results if row[2] >= 500]
        if bad:
            self.fail(f'Route smoke failures: {bad}')

        self.assertEqual(len(results), 62)


if __name__ == '__main__':
    unittest.main()
