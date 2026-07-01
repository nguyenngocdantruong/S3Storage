from infrastructure.persistence.models import ItemLike, UploadedFile, VideoNote, VideoProgress


def paste_single_file(src_conn, src_bucket, src_key, dest_conn, dest_bucket, dest_key, action, *, current_user_id, db_session, storage_provider_factory):
    src_s3 = storage_provider_factory(src_conn)
    dest_s3 = storage_provider_factory(dest_conn)

    if src_conn.id == dest_conn.id and src_bucket == dest_bucket and src_key == dest_key:
        return

    if src_conn.id == dest_conn.id:
        dest_s3.copy_object(
            Bucket=dest_bucket,
            CopySource={'Bucket': src_bucket, 'Key': src_key},
            Key=dest_key,
        )
    else:
        response = src_s3.get_object(Bucket=src_bucket, Key=src_key)
        dest_s3.upload_fileobj(
            response['Body'],
            Bucket=dest_bucket,
            Key=dest_key,
            ExtraArgs={'ContentType': response.get('ContentType', 'application/octet-stream')},
        )

    if action == 'move':
        src_s3.delete_object(Bucket=src_bucket, Key=src_key)
        UploadedFile.query.filter_by(connection_id=dest_conn.id, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)
        VideoProgress.query.filter_by(connection_name=dest_conn.name, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)
        VideoNote.query.filter_by(connection_name=dest_conn.name, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)
        ItemLike.query.filter_by(connection_name=dest_conn.name, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)

        uploaded_file = UploadedFile.query.filter_by(connection_id=src_conn.id, bucket_name=src_bucket, file_key=src_key).first()
        if uploaded_file:
            uploaded_file.connection_id = dest_conn.id
            uploaded_file.bucket_name = dest_bucket
            uploaded_file.file_key = dest_key
        else:
            db_session.add(UploadedFile(connection_id=dest_conn.id, bucket_name=dest_bucket, file_key=dest_key, user_id=current_user_id or 1))

        VideoProgress.query.filter_by(connection_name=src_conn.name, bucket_name=src_bucket, file_key=src_key).update({
            VideoProgress.connection_name: dest_conn.name,
            VideoProgress.bucket_name: dest_bucket,
            VideoProgress.file_key: dest_key,
            VideoProgress.file_name: dest_key.split('/')[-1],
        }, synchronize_session=False)
        VideoNote.query.filter_by(connection_name=src_conn.name, bucket_name=src_bucket, file_key=src_key).update({
            VideoNote.connection_name: dest_conn.name,
            VideoNote.bucket_name: dest_bucket,
            VideoNote.file_key: dest_key,
        }, synchronize_session=False)
        ItemLike.query.filter_by(connection_name=src_conn.name, bucket_name=src_bucket, file_key=src_key).update({
            ItemLike.connection_name: dest_conn.name,
            ItemLike.bucket_name: dest_bucket,
            ItemLike.file_key: dest_key,
        }, synchronize_session=False)
    else:
        UploadedFile.query.filter_by(connection_id=dest_conn.id, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)
        VideoProgress.query.filter_by(connection_name=dest_conn.name, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)
        VideoNote.query.filter_by(connection_name=dest_conn.name, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)
        ItemLike.query.filter_by(connection_name=dest_conn.name, bucket_name=dest_bucket, file_key=dest_key).delete(synchronize_session=False)

        src_uploaded_file = UploadedFile.query.filter_by(connection_id=src_conn.id, bucket_name=src_bucket, file_key=src_key).first()
        creator_id = src_uploaded_file.user_id if src_uploaded_file else (current_user_id or 1)
        db_session.add(UploadedFile(connection_id=dest_conn.id, bucket_name=dest_bucket, file_key=dest_key, user_id=creator_id))
