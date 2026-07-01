from infrastructure.persistence.models import S3Connection, UserBucket


def get_user_storage_used(user, *, db_session, storage_provider_factory):
    user_buckets = UserBucket.query.filter_by(user_id=user.id).all()
    total_size = 0
    client_cache = {}

    for user_bucket in user_buckets:
        conn = db_session.get(S3Connection, user_bucket.connection_id)
        if not conn:
            continue
        try:
            if conn.id not in client_cache:
                client_cache[conn.id] = storage_provider_factory(conn)
            s3 = client_cache[conn.id]

            paginator = s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=user_bucket.bucket_name)
            for page in pages:
                for obj in page.get('Contents', []):
                    total_size += obj.get('Size', 0)
        except Exception:
            pass

    return total_size
