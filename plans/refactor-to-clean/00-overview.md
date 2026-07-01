# Tổng Quan Kiến Trúc Sạch — S3VideoPlayer

## Tại Sao Refactor

`app.py` là một monolith 3.814 dòng. Route, model, thao tác S3, chuyển đổi file, và logic khởi động đều trộn lẫn trong một file duy nhất. Mục tiêu của refactor này:

- Mỗi phần có thể thay đổi độc lập (đổi S3 provider, đổi DB, thêm nhóm route mới)
- Tuân theo SOLID — đặc biệt là Dependency Inversion cho storage layer
- App vẫn hoạt động hoàn chỉnh sau mỗi phase (không rewrite toàn bộ cùng lúc)
- Giữ nguyên Docker, CI/CD, và toàn bộ URL hiện tại

---

## Sơ Đồ Các Lớp

```
┌──────────────────────────────────────────────────────┐
│  interfaces/  (Flask Blueprints, HTTP adapters)      │
│  • Parse request → gọi use_case → trả response      │
│  • Biết về: Flask, Jinja2, request, session, g       │
├──────────────────────────────────────────────────────┤
│  use_cases/   (Logic nghiệp vụ, không có Flask)      │
│  • access_control, audit, quota, file_type, v.v.     │
│  • Biết về: domain models, infrastructure            │
├──────────────────────────────────────────────────────┤
│  infrastructure/  (Triển khai cụ thể)                │
│  • persistence/: SQLAlchemy models + migrations      │
│  • storage/: Boto3StorageProvider                    │
│  • media/: ffmpeg, libreoffice subprocess wrappers   │
│  • Biết về: domain/ports/ interfaces                 │
├──────────────────────────────────────────────────────┤
│  domain/      (Python thuần, không có dep ngoài)     │
│  • ports/storage.py: StorageProvider ABC             │
│  • exceptions.py: các loại lỗi thuộc domain          │
│  • Không biết gì ngoài stdlib Python                 │
└──────────────────────────────────────────────────────┘
```

## Quy Tắc Phụ Thuộc

```
interfaces  →  use_cases  →  infrastructure  →  domain
```

- `domain` không import gì ngoài stdlib
- `infrastructure` chỉ import từ `domain/ports/`
- `use_cases` import từ `domain/` và `infrastructure/persistence/models`
- `interfaces` import từ tất cả các lớp

**Không được import ngược chiều.** Một `use_case` không được import từ `interfaces`. Một module `infrastructure` không được import từ `use_cases`.

---

## Thứ Gì Thay Đổi, Thứ Gì Giữ Nguyên

| Mục | Trạng thái |
|-----|-----------|
| Framework Flask | Không đổi |
| SQLAlchemy + SQLite | Không đổi |
| 61 URL route (đường dẫn HTTP) | Không đổi |
| Templates (16 file HTML) | Không đổi (tên endpoint trong url_for cập nhật ở Phase 4) |
| Static assets | Không đổi |
| Dockerfile / docker-compose.yml | Không đổi |
| `.github/workflows/deploy.yml` | Không đổi |
| `deploy.sh` | Không đổi |
| Entrypoint `gunicorn app:app` | Không đổi |
| `app.py` | Thu lại còn 5 dòng |
| Pattern inline DB migrations | Giữ nguyên (không dùng Alembic) |
| import boto3 | Tách riêng vào `infrastructure/storage/boto3_provider.py` |
| Gọi ffmpeg/libreoffice | Tách riêng vào `infrastructure/media/` |

---

## Nguyên Tắc SOLID Được Áp Dụng

**S — Single Responsibility (Trách nhiệm Đơn lẻ)**: Mỗi module chỉ có một lý do để thay đổi. `access_control.py` chỉ thay đổi khi quy tắc nghiệp vụ về quyền truy cập bucket thay đổi. `boto3_provider.py` chỉ thay đổi khi S3 API thay đổi.

**O — Open/Closed (Mở/Đóng)**: Provider storage mới (MinIO native SDK, local filesystem) mở rộng `StorageProvider` mà không sửa code hiện có.

**L — Liskov Substitution (Thay thế Liskov)**: Bất kỳ implementation nào của `StorageProvider` đều có thể hoán đổi trong `get_storage_provider()` mà không cần sửa code gọi.

**I — Interface Segregation (Phân tách Interface)**: `StorageProvider` là interface duy nhất cần thiết. Route handler không phụ thuộc vào toàn bộ boto3 API.

**D — Dependency Inversion (Đảo ngược Phụ thuộc)**: Route handler phụ thuộc vào `StorageProvider` (trừu tượng), không phải `boto3.client` (cụ thể). `Boto3StorageProvider` cụ thể được inject qua factory `get_storage_provider()`.
