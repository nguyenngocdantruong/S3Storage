from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    dob = db.Column(db.String(50))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="User")
    quota_limit = db.Column(db.BigInteger, default=2147483648)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class S3Connection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    endpoint_url = db.Column(db.String(255), nullable=False)
    access_key = db.Column(db.String(255), nullable=False)
    secret_key = db.Column(db.String(255), nullable=False)
    region_name = db.Column(db.String(100), default="us-east-1")
    upload_endpoint = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    owner = db.relationship("User", backref=db.backref("owned_connections", lazy=True))

    def __repr__(self):
        return f"<S3Connection {self.name}>"


class UserBucket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey("s3_connection.id"), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    access_type = db.Column(db.String(20), default="restricted")
    bucket_size = db.Column(db.BigInteger, default=0, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("owned_buckets", lazy=True))
    connection = db.relationship("S3Connection", backref=db.backref("mapped_buckets", lazy=True))


class BucketAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey("s3_connection.id"), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default="Viewer")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("shared_accesses", cascade="all, delete-orphan"))
    connection = db.relationship("S3Connection", backref=db.backref("shared_accesses", cascade="all, delete-orphan"))


class VideoProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    connection_name = db.Column(db.String(100), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    seconds_watched = db.Column(db.Float, default=0.0)
    duration = db.Column(db.Float, default=0.0)
    last_watched_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("video_progresses", lazy=True))


class VideoNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    connection_name = db.Column(db.String(100), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.Float, nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("video_notes", lazy=True))


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    target_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    connection_name = db.Column(db.String(100))
    bucket_name = db.Column(db.String(100))
    action_type = db.Column(db.String(50))
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    actor = db.relationship("User", foreign_keys=[user_id], backref=db.backref("actions_logged", lazy=True))
    target_owner = db.relationship("User", foreign_keys=[target_user_id], backref=db.backref("target_logs", lazy=True))


class UploadedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey("s3_connection.id"), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("uploaded_files", lazy=True))
    connection = db.relationship("S3Connection", backref=db.backref("uploaded_files", lazy=True))


class ItemLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_name = db.Column(db.String(100), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(255), nullable=False)
    like_count = db.Column(db.Integer, default=0, nullable=False)


class S3FileIndex(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey("s3_connection.id"), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(512), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    size = db.Column(db.BigInteger, default=0)
    last_modified = db.Column(db.DateTime, nullable=True)

    connection = db.relationship("S3Connection", backref=db.backref("file_indexes", lazy=True, cascade="all, delete-orphan"))
