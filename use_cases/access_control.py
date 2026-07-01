from infrastructure.persistence.models import BucketAccess, UploadedFile, UserBucket


PUBLIC_ACCESS_TYPES = ['public', 'public_view', 'public_edit', 'public_upload']
EDITABLE_PUBLIC_ACCESS_TYPES = ['public_edit', 'public_upload']


def check_bucket_access(user, connection, bucket_name):
    mapping = UserBucket.query.filter_by(connection_id=connection.id, bucket_name=bucket_name).first()
    if mapping and mapping.access_type in PUBLIC_ACCESS_TYPES:
        return True

    if not user:
        return False
    if user.role == 'Admin':
        return True
    if mapping and mapping.user_id == user.id:
        return True

    shared = BucketAccess.query.filter_by(
        user_id=user.id,
        connection_id=connection.id,
        bucket_name=bucket_name,
    ).first()
    return bool(shared)


def check_bucket_edit_access(user, connection, bucket_name):
    if not user:
        return False
    if user.role == 'Admin':
        return True

    mapping = UserBucket.query.filter_by(connection_id=connection.id, bucket_name=bucket_name).first()
    if mapping and mapping.user_id == user.id:
        return True
    if mapping and mapping.access_type in EDITABLE_PUBLIC_ACCESS_TYPES:
        return True

    shared = BucketAccess.query.filter_by(
        user_id=user.id,
        connection_id=connection.id,
        bucket_name=bucket_name,
    ).first()
    return bool(shared and shared.role == 'Editor')


def check_file_edit_access(user, connection, bucket_name, file_key):
    if not user:
        return False
    if user.role == 'Admin':
        return True

    mapping = UserBucket.query.filter_by(connection_id=connection.id, bucket_name=bucket_name).first()
    if mapping and mapping.user_id == user.id:
        return True
    if mapping and mapping.access_type == 'public_edit':
        return True

    shared = BucketAccess.query.filter_by(
        user_id=user.id,
        connection_id=connection.id,
        bucket_name=bucket_name,
    ).first()
    if shared and shared.role == 'Editor':
        return True

    if mapping and mapping.access_type == 'public_upload':
        if not file_key:
            return False
        if file_key.endswith('/'):
            uploaded_by_others = UploadedFile.query.filter(
                UploadedFile.connection_id == connection.id,
                UploadedFile.bucket_name == bucket_name,
                UploadedFile.file_key.startswith(file_key),
                UploadedFile.user_id != user.id,
            ).first()
            return not bool(uploaded_by_others)

        uploaded_file = UploadedFile.query.filter_by(
            connection_id=connection.id,
            bucket_name=bucket_name,
            file_key=file_key,
        ).first()
        return bool(uploaded_file and uploaded_file.user_id == user.id)

    return False
