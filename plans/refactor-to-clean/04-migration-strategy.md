# Chiến Lược Migration Database

## Quyết Định: Giữ Inline Migrations (Không Dùng Alembic)

**Lý do**:
1. SQLite không có `ALTER TABLE IF NOT EXISTS` — pattern check-then-alter là cách đúng với SQLite
2. Pattern hiện tại là idempotent: an toàn khi chạy mỗi lần khởi động
3. Alembic yêu cầu bảng lịch sử migration, file version được tạo tự động, và migration runner — tăng độ phức tạp không cần thiết cho dự án cá nhân
4. 8 migration hiện tại đã chạy trên tất cả database production; chúng đã ổn định

---

## Pattern Chuẩn

Mỗi migration tuân theo đúng hình dạng này:

```python
def _migrate_tablename_colname():
    try:
        db.session.execute(db.text("SELECT colname FROM tablename LIMIT 1")).fetchone()
        db.session.rollback()   # cột đã tồn tại, không làm gì
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text(
                "ALTER TABLE tablename ADD COLUMN colname TYPE DEFAULT val"
            ))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Lỗi migration tablename.colname: {e}")
```

`try` ngoài phát hiện cột bằng cách query nó. Nếu raise (cột chưa có), khối `except` thêm cột vào.

---

## Các Migration Hiện Có (theo thứ tự thực thi)

| Hàm | Bảng | Cột | Giá trị mặc định | Dòng nguồn |
|-----|------|-----|------------------|------------|
| `_migrate_user_quota_limit` | user | quota_limit | 2147483648 (2GB) | app.py:427-437 |
| `_migrate_user_is_active` | user | is_active | 1 (True) | app.py:439-449 |
| `_migrate_s3connection_connection_id` | s3_connection | connection_id | — (sau đó tạo slug) | app.py:451-477 |
| `_migrate_s3connection_upload_endpoint` | s3_connection | upload_endpoint | NULL | app.py:479-489 |
| `_migrate_s3connection_owner_id` | s3_connection | owner_id | NULL (sau đó backfill thành admin) | app.py:491-507 |
| `_migrate_userbucket_access_type` | user_bucket | access_type | 'restricted' | app.py:509-519 |
| `_migrate_userbucket_bucket_size` | user_bucket | bucket_size | 0 | app.py:521-531 |
| `_migrate_bucketaccess_role` | bucket_access | role | 'Viewer' | app.py:533-543 |

**Quan trọng**: `_migrate_s3connection_connection_id` có bước data migration sau ALTER TABLE — tạo slug URL-safe cho tất cả connection hiện có. Phải chạy trước `_migrate_s3connection_owner_id` vì migration này tham chiếu connection theo `id`.

---

## Bootstrap Khi Khởi Động (chạy sau migrations)

```
run_startup_migrations(app)
  ├── db.create_all()                   — tạo bảng cho cài đặt mới
  ├── Bật WAL mode                      — cải thiện đồng thời SQLite
  ├── _migrate_user_quota_limit()
  ├── _migrate_user_is_active()
  ├── _migrate_s3connection_connection_id()
  ├── _migrate_s3connection_upload_endpoint()
  ├── _migrate_s3connection_owner_id()
  ├── _migrate_userbucket_access_type()
  ├── _migrate_userbucket_bucket_size()
  ├── _migrate_bucketaccess_role()
  ├── _seed_admin_from_config()         — chỉ chạy khi bảng user trống
  └── _sync_unassigned_buckets_to_admin()  — map bucket S3 chưa có trong DB về admin
```

---

## Cách Thêm Migration Mới

1. Thêm hàm private mới ở cuối `migrations.py`:

```python
def _migrate_tablename_column_moi():
    try:
        db.session.execute(db.text("SELECT column_moi FROM tablename LIMIT 1")).fetchone()
        db.session.rollback()
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text(
                "ALTER TABLE tablename ADD COLUMN column_moi VARCHAR(50) DEFAULT 'gia_tri'"
            ))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Lỗi migration tablename.column_moi: {e}")
```

2. Gọi nó trong `run_startup_migrations(app)` sau các migration hiện có:

```python
def run_startup_migrations(app):
    with app.app_context():
        db.create_all()
        # ... migration hiện có ...
        _migrate_bucketaccess_role()
        _migrate_tablename_column_moi()   # ← thêm ở đây
        _seed_admin_from_config()
        _sync_unassigned_buckets_to_admin()
```

3. Cập nhật `infrastructure/persistence/models.py` để thêm cột mới vào SQLAlchemy model.

---

## WAL Mode

```python
try:
    db.session.execute(db.text("PRAGMA journal_mode=WAL;"))
    db.session.commit()
except Exception as e:
    print(f"Không thể bật WAL mode: {e}")
```

WAL (Write-Ahead Logging) cho phép đọc đồng thời trong khi đang ghi. Quan trọng vì Flask dev server và các worker gunicorn có thể truy cập DB cùng lúc. Chạy mỗi lần khởi động — SQLite chấp nhận re-set idempotent.

---

## Logic Seed Admin

`_seed_admin_from_config()` chỉ chạy khi bảng `user` trống (cài đặt mới). Nó đọc thông tin đăng nhập từ `config.conf`:

```ini
[ADMIN]
email = admin@example.com
password = mat_khau_an_toan
fullname = Administrator
dob = 1990-01-01
```

Dùng `admin@example.com` / `admin123` làm mặc định nếu file không tồn tại. File `config.conf` được volume-mount trong Docker nên tồn tại sau khi rebuild container.

---

## Startup Bucket Sync

`_sync_unassigned_buckets_to_admin()` chạy mỗi lần khởi động:
- Lặp qua tất cả bản ghi `S3Connection`
- Liệt kê bucket qua S3 API
- Bucket nào chưa có trong `UserBucket` sẽ được map về user Admin đầu tiên với `access_type='restricted'`
- Bucket đã map cho Admin sẽ được cập nhật `bucket_size`

Đây cố ý là thao tác blocking khi khởi động (không phải background task) để đảm bảo admin luôn thấy tất cả bucket sau khi thêm connection mới.
