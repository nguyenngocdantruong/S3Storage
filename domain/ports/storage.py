from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StorageObject:
    key: str
    size: int = 0
    last_modified: Any = None
    content_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PresignedPost:
    url: str
    fields: dict[str, str]


class StorageProvider(ABC):
    @abstractmethod
    def list_buckets(self):
        raise NotImplementedError

    @abstractmethod
    def head_object(self, bucket: str, key: str):
        raise NotImplementedError

    @abstractmethod
    def list_objects(self, bucket: str, prefix: str = ''):
        raise NotImplementedError

    @abstractmethod
    def copy_object(self, source_bucket: str, source_key: str, dest_bucket: str, dest_key: str):
        raise NotImplementedError

    @abstractmethod
    def delete_object(self, bucket: str, key: str):
        raise NotImplementedError

    @abstractmethod
    def generate_presigned_url(self, operation: str, *, params: dict[str, Any], expires_in: int = 3600):
        raise NotImplementedError
