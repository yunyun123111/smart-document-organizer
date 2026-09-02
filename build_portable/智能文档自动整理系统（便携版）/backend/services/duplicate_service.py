"""重复文件检测服务（规格书第三十二节）。

使用 SHA256 识别完全相同文件。
- 已归档（documents.status=archived 且 hash 相同）→ duplicate
- 用户可选择：保留原文件 / 跳过 / 移动到重复文件夹（由上层处理）
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Document, STATUS_ARCHIVED
from backend.utils.hash_utils import sha256_file
from backend.utils.logger import get_logger

logger = get_logger("services.duplicate_service")


class DuplicateService:
    def file_hash(self, path) -> str:
        return sha256_file(path)

    def find_duplicate(self, db: Session, file_hash: str) -> Document | None:
        """在已归档文档中查找相同 hash 的记录。"""
        return (
            db.query(Document)
            .filter(
                Document.file_hash == file_hash,
                Document.status == STATUS_ARCHIVED,
            )
            .first()
        )

    def check(self, db: Session, path) -> tuple[bool, Document | None]:
        """检查文件是否重复。返回 (is_duplicate, 已存在文档或 None)。"""
        file_hash = self.file_hash(path)
        dup = self.find_duplicate(db, file_hash)
        if dup is not None:
            logger.info("发现重复文件: %s 与文档#%d 相同", path, dup.id)
            return True, dup
        return False, None


duplicate_service = DuplicateService()
