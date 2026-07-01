# Bảng Đánh Giá Rủi Ro

| Phase | Rủi ro | Mức độ | Biện pháp xử lý | Trạng thái |
|-------|--------|--------|-----------------|-----------|
| **Phase 1** | Circular imports: `models.py` ↔ `app.py` ↔ `migrations.py` | Trung bình | Dùng `extensions.py` làm singleton chung. `models.py` import `db` từ `extensions`. `migrations.py` import `db` từ `extensions` và models từ `infrastructure.persistence.models`. Không bao giờ import từ `app.py` trong bất kỳ module mới nào. | Mở |
| **Phase 1** | `_sync_unassigned_buckets_to_admin()` gọi `get_s3_client()` vẫn còn trong `app.py` | Thấp | Giữ startup bucket sync trong `app.py` tạm thời ở Phase 1. Chuyển sang `migrations.py` ở Phase 2 khi `get_storage_provider()` đã tồn tại trong infrastructure. | Mở |
| **Phase 1** | `db.create_all()` và migrations cần app context — nếu gọi trước `init_app` sẽ lỗi im lặng | Thấp | Luôn wrap toàn bộ code migration trong `with app.app_context():`. Hàm `run_startup_migrations(app)` nhận `app` tường minh. | Mở |
| **Phase 2** | `fix_s3_url()` dùng object `request` — không thể đặt trong infrastructure layer | Cao | Đổi tên thành `fix_url(url, is_https)`. Cập nhật 5 call site trong `app.py` để trích `is_https` từ `request` trước khi gọi. Grep: `grep -n "fix_s3_url" app.py` để tìm tất cả vị trí. | Mở |
| **Phase 2** | `paste_single_file()` dùng trực tiếp `g.user.id` (Flask global) | Trung bình | Đổi signature hàm để nhận `current_user_id: int`. Cập nhật caller duy nhất (route `paste_selected_items`) để truyền `g.user.id`. | Mở |
| **Phase 2** | `delete_bucket` có logic fallback versioned-objects phức tạp | Trung bình | Giữ nguyên pattern try/except versioned → liệt kê thông thường từ app.py trong `Boto3StorageProvider.delete_objects()`. Không đơn giản hóa. | Mở |
| **Phase 2** | ffmpeg/libreoffice chỉ có trong Docker | Trung bình | Các lời gọi subprocess đã được bọc trong try/except trong route functions. Giữ nguyên pattern xử lý lỗi trong các media wrapper mới. | Mở |
| **Phase 3** | Access control functions query `UploadedFile.query` — cần import ORM models | Thấp | `use_cases/access_control.py` import model class từ `infrastructure.persistence.models`. Đây là tham chiếu ngược hợp lệ duy nhất (use_cases → infrastructure). | Mở |
| **Phase 3** | Gộp file-type classification có thể bỏ sót một extension | Thấp | Viết script test nhanh trước khi nhúng: đưa vào các extension đã biết từ danh sách hiện tại và kiểm tra output. Danh sách cũ ở app.py:2116-2138 và 3263-3285. | Mở |
| **Phase 4** | Bỏ sót cập nhật `url_for()` gây `BuildError` lúc runtime | Cao | Sau mỗi bước blueprint nhỏ, grep tên endpoint CŨ trong tất cả template và file Python. Phải trả về 0 kết quả trước khi sang bước tiếp theo. | Mở |
| **Phase 4** | `browser.html` có 40+ lời gọi url_for trong khối `<script>` | Cao | Kiểm kê TẤT CẢ lời gọi url_for trong browser.html trước bước 4e và 4f: `grep -n "url_for" templates/browser.html`. Cập nhật tất cả trong một lần theo từng bước blueprint. | Mở |
| **Phase 4** | Blueprint đăng ký không có url_prefix nhưng route vô tình trùng với blueprint khác | Trung bình | Sau mỗi lần đăng ký blueprint, kiểm tra `app.url_map` để phát hiện entry trùng lặp. | Mở |
| **Phase 4** | `login_required` trong `interfaces/middleware/context.py` gọi `url_for('login')` — phải cập nhật thành `url_for('auth.login')` trước khi blueprint auth được đăng ký ở Phase 4a | Trung bình | Cập nhật decorator ở Phase 3 (trước Phase 4) để dùng `url_for('auth.login')`. Blueprint `auth` phải được đăng ký để decorator hoạt động — thực hiện ở bước 4a. | Mở |
| **Phase 4** | `flv_hls_playlist` gọi `url_for('flv_hls_segment', ...)` nội bộ | Thấp | Cả hai đều trong blueprint viewer. Đổi thành `url_for('viewer.flv_hls_segment', ...)` trong cùng file cùng lúc (bước 4g). | Mở |
| **Phase 4** | Error handlers (400/403/404/500) kiểm tra `request.path.startswith('/api/')` — phải ở trên `app`, không phải blueprint | Thấp | Giữ tất cả error handler trong `application.py`, không để trong blueprint nào. Chúng tự động bao phủ tất cả blueprint. | Mở |
| **Phase 5** | `gunicorn app:app` lỗi nếu `app` không ở module level trong `app.py` | Trung bình | `app.py` phải luôn có `app = create_app()` ở module level. Phiên bản 5 dòng cuối vẫn export `app`. | Mở |
| **Phase 5** | `python3 app.py` trong `deploy.sh` (tmux fallback) chạy Flask dev server | Thấp | Giữ `if __name__ == '__main__': app.run(...)` trong `app.py` cuối cùng. Không cần thay đổi `deploy.sh`. | Mở |
| **Phase 5** | Logging được cấu hình ở module level trong `app.py` hiện tại — nếu chuyển vào `create_app()`, lỗi import sớm sẽ không được log | Thấp | Cấu hình file handler là việc đầu tiên trong `create_app()`, trước bất kỳ khởi tạo nào khác. | Mở |

---

## Chú Thích Mức Độ

- **Cao**: Gây lỗi 500 hoặc route hỏng nếu bỏ sót — ảnh hưởng người dùng ngay lập tức
- **Trung bình**: Gây bug tinh tế hoặc lỗi khởi động cần điều tra
- **Thấp**: Vấn đề ngoại lệ hoặc edge case không ảnh hưởng hoạt động bình thường

---

## Theo Dõi

Cập nhật cột **Trạng thái** khi làm việc:
- `Mở` — chưa xử lý
- `Đã giảm thiểu` — biện pháp đã áp dụng, đang theo dõi
- `Đã đóng` — rủi ro đã giải quyết và xác minh
