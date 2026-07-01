# Interface StorageProvider

**File**: `domain/ports/storage.py`

Đây là abstraction cốt lõi. Tất cả thao tác S3/MinIO/R2 đều đi qua interface này. Nơi duy nhất có `import boto3` là `infrastructure/storage/boto3_provider.py`.

---

## Data Classes

```python
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

@dataclass
class StorageObject:
    key: str
    size: int
    last_modified: Optional[datetime]
    content_type: Optional[str] = None

@dataclass
class PresignedPost:
    url: str
    fields: Dict[str, str]
```

---

## Interface Trừu Tượng

```python
from abc import ABC, abstractmethod
from typing import Iterator, List, Optional, Dict, Any

class StorageProvider(ABC):

    # ── Thao tác Bucket ───────────────────────────────────────────────────
    @abstractmethod
    def list_buckets(self) -> List[Dict[str, Any]]:
        """Trả về danh sách bucket dạng dict, tối thiểu có {'Name': str}."""

    @abstractmethod
    def create_bucket(self, bucket: str, region: Optional[str] = None) -> None: ...

    @abstractmethod
    def delete_bucket(self, bucket: str) -> None: ...

    @abstractmethod
    def configure_cors(self, bucket: str) -> None:
        """Thiết lập CORS rules để trình duyệt upload trực tiếp."""

    @abstractmethod
    def get_bucket_size(self, bucket: str) -> Optional[int]:
        """Tổng kích thước tất cả object (bytes). Trả về None nếu lỗi."""

    # ── Liệt kê Object ────────────────────────────────────────────────────
    @abstractmethod
    def list_objects(self, bucket: str, prefix: str = '',
                     delimiter: str = '') -> Iterator[StorageObject]:
        """Danh sách object phân trang (không gồm folder). Dùng delimiter='/' để xem 1 cấp."""

    @abstractmethod
    def list_object_versions(self, bucket: str) -> Iterator[Dict]:
        """Dùng bởi delete_bucket để xóa object có phiên bản trước."""

    # ── CRUD Object ────────────────────────────────────────────────────────
    @abstractmethod
    def head_object(self, bucket: str, key: str) -> Dict[str, Any]:
        """Raise StorageError nếu object không tồn tại."""

    @abstractmethod
    def get_object(self, bucket: str, key: str,
                   range_header: Optional[str] = None) -> Dict[str, Any]:
        """Trả về dict gồm 'Body' (streaming), 'ContentLength', 'ContentType', v.v."""

    @abstractmethod
    def put_object(self, bucket: str, key: str, body: bytes,
                   content_type: str = 'application/octet-stream') -> None: ...

    @abstractmethod
    def copy_object(self, src_bucket: str, src_key: str,
                    dst_bucket: str, dst_key: str) -> None: ...

    @abstractmethod
    def delete_object(self, bucket: str, key: str) -> None: ...

    @abstractmethod
    def delete_objects(self, bucket: str, keys: List[str]) -> None:
        """Xóa hàng loạt. Tự chia thành chunk 1000 object."""

    @abstractmethod
    def object_exists(self, bucket: str, key: str) -> bool:
        """True nếu object tồn tại. Thay thế s3_key_exists() và mọi định nghĩa lại cục bộ."""

    @abstractmethod
    def upload_fileobj(self, fileobj, bucket: str, key: str,
                       content_type: str = 'application/octet-stream') -> None: ...

    @abstractmethod
    def download_file(self, bucket: str, key: str, local_path: str) -> None:
        """Tải object về đường dẫn cục bộ (dùng cho office→PDF và FLV→MP4)."""

    # ── Presigned URLs ─────────────────────────────────────────────────────
    @abstractmethod
    def generate_presigned_get_url(self, bucket: str, key: str,
                                   expires_in: int = 3600) -> str:
        """Dùng cho viewer, link chia sẻ, tạo URL HLS stream."""

    @abstractmethod
    def generate_presigned_post(self, bucket: str, key: str,
                                content_type: str, max_size: int,
                                expires_in: int = 3600) -> PresignedPost:
        """Dùng cho upload file đơn từ trình duyệt."""

    @abstractmethod
    def generate_presigned_part_url(self, bucket: str, key: str,
                                    upload_id: str, part_number: int,
                                    expires_in: int = 3600) -> str:
        """Dùng để presign từng part trong multipart upload."""

    # ── Multipart Upload ───────────────────────────────────────────────────
    @abstractmethod
    def create_multipart_upload(self, bucket: str, key: str,
                                content_type: str) -> str:
        """Trả về chuỗi upload_id."""

    @abstractmethod
    def complete_multipart_upload(self, bucket: str, key: str,
                                  upload_id: str,
                                  parts: List[Dict[str, Any]]) -> None:
        """parts: danh sách {'PartNumber': int, 'ETag': str}"""

    @abstractmethod
    def abort_multipart_upload(self, bucket: str, key: str,
                               upload_id: str) -> None: ...
```

---

## Triển Khai Cụ Thể: Boto3StorageProvider

**File**: `infrastructure/storage/boto3_provider.py`

```python
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from domain.ports.storage import StorageProvider, StorageObject, PresignedPost
from domain.exceptions import StorageError

class Boto3StorageProvider(StorageProvider):
    def __init__(self, access_key: str, secret_key: str,
                 endpoint_url: str, region_name: str):
        config = Config(
            signature_version='s3v4',
            retries={'max_attempts': 3},
            s3={'addressing_style': 'path'}
        )
        endpoint = endpoint_url.strip() if endpoint_url and endpoint_url.strip() else None
        self._client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name or 'us-east-1',
            config=config
        )

    def object_exists(self, bucket: str, key: str) -> bool:
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False

    # ... triển khai tất cả các method abstract còn lại


def get_storage_provider(connection, endpoint_url=None) -> Boto3StorageProvider:
    """Factory — route handler gọi hàm này để lấy provider theo từng request."""
    url = endpoint_url or connection.endpoint_url
    return Boto3StorageProvider(
        access_key=connection.access_key,
        secret_key=connection.secret_key,
        endpoint_url=url,
        region_name=connection.region_name,
    )


def fix_url(url: str, is_https: bool) -> str:
    """Đổi HTTP→HTTPS để tránh lỗi mixed-content. Gọi từ interface layer."""
    if is_https and url and url.startswith('http://'):
        return url.replace('http://', 'https://', 1)
    return url
```

---

## Pattern Dual-Endpoint

`S3Connection` có hai trường URL:
- `endpoint_url` — endpoint nội bộ/ghi (dùng cho upload, thao tác API)
- `upload_endpoint` — endpoint công khai/tải về tùy chọn (dùng để tạo presigned GET URL)

Route handler truyền endpoint phù hợp vào `get_storage_provider()`:

```python
# Cho thao tác API (upload, xóa, liệt kê):
provider = get_storage_provider(connection)

# Cho tạo URL tải về công khai:
provider = get_storage_provider(connection, endpoint_url=connection.upload_endpoint or connection.endpoint_url)
```

---

## Di Chuyển fix_s3_url

Hiện tại `app.py:221`: `fix_s3_url(url)` đọc `request.is_secure` trực tiếp — không thể đặt trong infrastructure.

**Sau refactor**: Mỗi trong 5 call site ở route handler làm như sau:

```python
from infrastructure.storage.boto3_provider import fix_url

is_https = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
url = fix_url(raw_url, is_https)
```

---

## Cách Thêm Provider Mới

Để thêm MinIO native provider (hoặc local filesystem):

1. Tạo `infrastructure/storage/minio_provider.py`
2. Implement `class MinioStorageProvider(StorageProvider): ...`
3. Sửa `get_storage_provider()` để trả về `MinioStorageProvider(...)` dựa theo loại connection
4. Không cần thay đổi gì trong `use_cases/` hay `interfaces/`
