# Checklist Các Phase Refactor

**Quy tắc**: Mỗi phase phải để app chạy hoàn chỉnh trước khi bắt đầu phase tiếp theo. Kiểm tra bằng `python app.py` + smoke test sau mỗi phase.

---

## Phase 0 — Tạo Khung Thư Mục (không thay đổi hành vi)

- [ ] Tạo tất cả thư mục: `domain/`, `domain/ports/`, `infrastructure/`, `infrastructure/persistence/`, `infrastructure/storage/`, `infrastructure/media/`, `use_cases/`, `interfaces/middleware/`, `interfaces/auth/`, `interfaces/main/`, `interfaces/connections/`, `interfaces/buckets/`, `interfaces/files/`, `interfaces/viewer/`, `interfaces/progress/`, `interfaces/admin/`, `interfaces/api/`
- [ ] Thêm `__init__.py` vào mỗi thư mục mới
- [ ] Tạo `extensions.py` với `db = SQLAlchemy()` (chưa kết nối với app)
- [ ] **Kiểm tra**: `python app.py` khởi động bình thường. Không thay đổi hành vi.

---

## Phase 1 — Tách Models + Migrations

- [ ] Tạo `infrastructure/persistence/models.py` — paste toàn bộ 10 model class từ `app.py:93-217`; import `db` từ `extensions`
- [ ] Tạo `infrastructure/persistence/migrations.py` — tách `app.py:418-610` thành `run_startup_migrations(app)` với các hàm private `_migrate_*`
  - [ ] `_migrate_user_quota_limit()` — app.py:427-437
  - [ ] `_migrate_user_is_active()` — app.py:439-449
  - [ ] `_migrate_s3connection_connection_id()` — app.py:451-477 (gồm vòng lặp tạo slug)
  - [ ] `_migrate_s3connection_upload_endpoint()` — app.py:479-489
  - [ ] `_migrate_s3connection_owner_id()` — app.py:491-507
  - [ ] `_migrate_userbucket_access_type()` — app.py:509-519
  - [ ] `_migrate_userbucket_bucket_size()` — app.py:521-531
  - [ ] `_migrate_bucketaccess_role()` — app.py:533-543
  - [ ] `_seed_admin_from_config()` — app.py:545-575
  - [ ] `_sync_unassigned_buckets_to_admin()` — app.py:577-610 (tạm thời vẫn gọi `get_s3_client` từ app.py)
- [ ] Cập nhật `app.py`: thay `db = SQLAlchemy(app)` bằng `db.init_app(app)` từ extensions; import models; gọi `run_startup_migrations(app)`
- [ ] **Kiểm tra**: App khởi động, migrations chạy, tất cả route hoạt động, DB đúng.

---

## Phase 2 — Tách Infrastructure: Storage + Media

- [ ] Tạo `domain/exceptions.py` với `StorageError`, `QuotaExceededError`, `AccessDeniedError`
- [ ] Tạo `domain/ports/storage.py` với `StorageProvider` ABC, `StorageObject`, `PresignedPost`
- [ ] Tạo `infrastructure/storage/boto3_provider.py`:
  - [ ] Class `Boto3StorageProvider` implement tất cả method ABC
  - [ ] Factory `get_storage_provider(connection, endpoint_url=None)`
  - [ ] Hàm tiện ích `fix_url(url, is_https)`
- [ ] Cập nhật 5 call site `fix_s3_url()` trong `app.py` theo pattern `fix_url(url, is_https)`
- [ ] Thay thế các lời gọi `get_s3_client()` trong `app.py` bằng `get_storage_provider()`
- [ ] Chuyển `get_user_storage_used()` sang `use_cases/quota.py`
- [ ] Chuyển `paste_single_file()` (app.py:3629-3718) sang `use_cases/file_ops.py`; đổi `g.user.id` → tham số `current_user_id: int`
- [ ] Tạo `infrastructure/media/ffmpeg.py`:
  - [ ] `probe_video_duration(url) -> float`
  - [ ] `start_hls_segment_transcode(input_url, start, duration) -> subprocess.Popen`
  - [ ] `start_flv_to_mp4_transcode(input_path) -> subprocess.Popen`
- [ ] Tạo `infrastructure/media/libreoffice.py`:
  - [ ] `convert_to_pdf(input_path, output_dir) -> str`
- [ ] **Kiểm tra**: Video streaming, upload, chuyển đổi file, tính toán dung lượng đều hoạt động.

---

## Phase 3 — Tách Use Cases

- [ ] Tạo `use_cases/access_control.py`:
  - [ ] `check_bucket_access(user, connection, bucket_name)` — app.py:311-332
  - [ ] `check_bucket_edit_access(user, connection, bucket_name)` — app.py:334-354
  - [ ] `check_file_edit_access(user, connection, bucket_name, file_key)` — app.py:356-399
- [ ] Tạo `use_cases/audit.py`: `log_action(...)` — app.py:402-415
- [ ] Tạo `use_cases/file_type.py`: `classify_file_type(ext) -> str` — gộp app.py:2116-2138 và 3263-3285
- [ ] Tạo `use_cases/slug.py`: `generate_unique_slug(name, existing_slugs)` — app.py:962-984
- [ ] Chuyển `login_required`, `admin_required`, context processors sang `interfaces/middleware/context.py`
  - [ ] Chủ động cập nhật `url_for('login')` → `url_for('auth.login')` trong cả hai decorator (chuẩn bị cho Phase 4a)
- [ ] **Kiểm tra**: Kiểm soát truy cập, duyệt file, audit log, tạo slug đều hoạt động.

---

## Phase 4 — Flask Blueprints

**Sau mỗi bước nhỏ**: grep tên endpoint cũ trong tất cả file — phải trả về 0 kết quả.

```bash
grep -rn "url_for('tên_endpoint')" templates/ interfaces/ app.py
```

- [ ] **4a — blueprint auth** (`/register`, `/login`, `/logout`, `/profile`)
  - [ ] Tạo `interfaces/auth/views.py`
  - [ ] Cập nhật tất cả template: `login.html`, `register.html`, `base.html`
  - [ ] Cập nhật `login_required` và `admin_required`: `url_for('auth.login')`
  - [ ] Kiểm tra grep: `url_for('login')` → 0 kết quả

- [ ] **4b — blueprint admin** (`/admin/*`, `/logs`, `/admin/system-logs`)
  - [ ] Tạo `interfaces/admin/views.py`
  - [ ] Cập nhật template: `base.html`, `users.html`, `bucket_access.html`, `system_logs.html`, `logs.html`
  - [ ] Chủ động cập nhật redirect trong `admin_required`: `url_for('main.dashboard')`
  - [ ] Kiểm tra grep: tên endpoint admin cũ → 0 kết quả

- [ ] **4c — blueprint main** (`/`, `/search`, `/search/sync`)
  - [ ] Tạo `interfaces/main/views.py`
  - [ ] Cập nhật template: `base.html`, `search.html`
  - [ ] Kiểm tra grep: `url_for('dashboard')`, `url_for('global_search')` → 0 kết quả

- [ ] **4d — blueprint connections** (CRUD `/connection/<id>`)
  - [ ] Tạo `interfaces/connections/views.py`
  - [ ] Cập nhật template: `dashboard.html`, `buckets.html`
  - [ ] Kiểm tra grep: `url_for('add_connection')`, `url_for('view_connection')` → 0 kết quả

- [ ] **4e — blueprint buckets** (`browse`, `create`, `delete`)
  - [ ] Tạo `interfaces/buckets/views.py`
  - [ ] Cập nhật template: `buckets.html`, `browser.html`, `viewer.html`, `search.html`, `progress.html`
  - [ ] **Cảnh báo**: `browser.html` có 40+ lời gọi url_for — kiểm kê trước khi bắt đầu
  - [ ] Kiểm tra grep: `url_for('browse_bucket')` → 0 kết quả

- [ ] **4f — blueprint files** (multipart upload, đổi tên, xóa, paste, zip, folder)
  - [ ] Tạo `interfaces/files/views.py`
  - [ ] Cập nhật template: `browser.html` (các lời gọi fetch() trong khối `<script>`)
  - [ ] Kiểm tra grep: `url_for('multipart_initiate')` v.v. → 0 kết quả

- [ ] **4g — blueprint viewer** (xem file, proxy, HLS, FLV→MP4, office→PDF)
  - [ ] Tạo `interfaces/viewer/views.py`
  - [ ] Sửa `url_for('flv_hls_segment')` nội bộ → `url_for('viewer.flv_hls_segment')` trong cùng file
  - [ ] Cập nhật template: `browser.html`, `viewer.html`, `search.html`, `progress.html`
  - [ ] Kiểm tra grep: `url_for('view_file')`, `url_for('proxy_s3_file')` → 0 kết quả

- [ ] **4h — blueprint progress** (`/progress`, `/video/progress`, `/api/like`)
  - [ ] Tạo `interfaces/progress/views.py`
  - [ ] Cập nhật template: `base.html`, `progress.html`
  - [ ] Kiểm tra grep: `url_for('list_progress')` → 0 kết quả

- [ ] **4i — blueprint api** (tất cả route `/api/*`)
  - [ ] Tạo `interfaces/api/views.py`
  - [ ] Cập nhật template: `browser.html`, `search.html`
  - [ ] Kiểm tra grep: `url_for('get_bucket_share_info')`, `url_for('api_bucket_files')` → 0 kết quả

- [ ] **Kiểm tra Phase 4**: `print(app.url_map)` không có route trùng; smoke test toàn bộ tính năng.

---

## Phase 5 — Application Factory

- [ ] Tạo `application.py` với `create_app()`:
  - [ ] Cấu hình logging
  - [ ] Load config (secret key, DB path)
  - [ ] Đăng ký error handlers
  - [ ] `db.init_app(app)`
  - [ ] Đăng ký middleware từ `interfaces/middleware/context.py`
  - [ ] Đăng ký cả 9 blueprint
  - [ ] Gọi `run_startup_migrations(app)`
- [ ] Thu gọn `app.py` còn 5 dòng (`app = create_app()` + dev runner)
- [ ] **Kiểm tra**: `docker-compose up --build` thành công, tất cả route hoạt động, push CI/CD kích hoạt deploy.

---

## Hoàn Thành Khi

- [ ] `app.py` ≤ 10 dòng
- [ ] `grep -rn "import boto3" .` chỉ trả về `infrastructure/storage/boto3_provider.py`
- [ ] `grep -rn "import subprocess" .` chỉ trả về các file trong `infrastructure/media/`
- [ ] 61 route phản hồi đúng (kiểm tra thủ công)
- [ ] Docker build thành công
