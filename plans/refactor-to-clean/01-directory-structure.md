# Cấu Trúc Thư Mục Mục Tiêu

```
S3VideoPlayer/
│
├── app.py                              # 5 dòng: app = create_app() + dev server runner
├── application.py                      # Factory create_app() — kết nối tất cả các lớp
├── extensions.py                       # db = SQLAlchemy() singleton (dùng chung toàn bộ module)
├── config.py                           # Đọc config.conf + biến môi trường vào Config object
│
├── domain/
│   ├── __init__.py
│   ├── exceptions.py                   # StorageError, QuotaExceededError, AccessDeniedError
│   └── ports/
│       ├── __init__.py
│       └── storage.py                  # StorageProvider ABC + StorageObject/PresignedPost dataclasses
│
├── infrastructure/
│   ├── __init__.py
│   ├── persistence/
│   │   ├── __init__.py
│   │   ├── models.py                   # 10 SQLAlchemy ORM models (chuyển từ app.py:93-217)
│   │   └── migrations.py              # run_startup_migrations(app): 8 ALTER TABLE + seed admin + sync bucket
│   ├── storage/
│   │   ├── __init__.py
│   │   └── boto3_provider.py          # Boto3StorageProvider implements StorageProvider; factory get_storage_provider(); fix_url()
│   └── media/
│       ├── __init__.py
│       ├── ffmpeg.py                  # probe_video_duration(), start_hls_segment_transcode(), start_flv_to_mp4()
│       └── libreoffice.py             # convert_to_pdf(input_path, output_dir) → chuỗi pdf_path
│
├── use_cases/
│   ├── __init__.py
│   ├── access_control.py              # check_bucket_access/edit_access/file_edit_access (app.py:311-399)
│   ├── audit.py                       # log_action() (app.py:402-415)
│   ├── quota.py                       # get_user_storage_used(), enforce_quota()
│   ├── file_type.py                   # classify_file_type(ext) — gộp 2 chỗ mapping extension bị trùng lặp
│   ├── file_ops.py                    # paste_single_file() đã tách khỏi Flask (app.py:3629-3718)
│   └── slug.py                        # generate_unique_slug(name, existing_slugs) → str
│
├── interfaces/
│   ├── __init__.py
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── context.py                 # login_required, admin_required, load_logged_in_user, inject_quota, inject_g
│   ├── auth/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'auth': /register /login /logout /profile
│   ├── main/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'main': / /search /search/sync
│   ├── connections/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'connections': CRUD /connection/<id>
│   ├── buckets/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'buckets': duyệt, tạo, xóa bucket
│   ├── files/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'files': upload multipart, đổi tên, xóa, paste, zip, folder
│   ├── viewer/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'viewer': xem file, proxy, HLS stream, FLV→MP4, office→PDF
│   ├── progress/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'progress': /progress /video/progress /api/like
│   ├── admin/
│   │   ├── __init__.py
│   │   └── views.py                   # Blueprint 'admin': /admin/* /logs /admin/system-logs
│   └── api/
│       ├── __init__.py
│       └── views.py                   # Blueprint 'api': tất cả route /api/* (share, notes, paste, tìm user)
│
├── plans/
│   └── refactor-to-clean/             # Thư mục tài liệu này
│
├── templates/                         # KHÔNG ĐỔI — 16 file HTML Jinja2
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   ├── profile.html
│   ├── dashboard.html
│   ├── buckets.html
│   ├── browser.html
│   ├── viewer.html
│   ├── search.html
│   ├── progress.html
│   ├── users.html
│   ├── bucket_access.html
│   ├── logs.html
│   ├── system_logs.html
│   ├── admin_functions.html
│   └── error.html
│
├── static/                            # KHÔNG ĐỔI
│   ├── css/style.css
│   ├── js/main.js
│   └── logo.png
│
├── Dockerfile                         # KHÔNG ĐỔI — CMD: gunicorn app:app vẫn hoạt động
├── docker-compose.yml                 # KHÔNG ĐỔI
├── requirements.txt                   # KHÔNG ĐỔI
├── config.conf                        # KHÔNG ĐỔI (volume-mount trong Docker)
├── deploy.sh                          # KHÔNG ĐỔI
└── .github/
    └── workflows/
        └── deploy.yml                 # KHÔNG ĐỔI
```

## Tổng Hợp Số File

| Lớp | Số file |
|-----|---------|
| domain | 3 |
| infrastructure | 6 |
| use_cases | 6 |
| interfaces | 20 (10 blueprint × 2 file mỗi cái) |
| Root | 4 (app.py, application.py, extensions.py, config.py) |
| tests | 14 (packages + grouped test modules) |
| **Tổng file Python mới** | **39** |

Thay thế file `app.py` đơn độc 3.814 dòng.
