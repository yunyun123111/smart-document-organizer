"""重复文件检测服务（规格书第三十二节）。

使用 SHA256 识别完全相同文件。
- 同 hash 的文件只要已「定性」（已归档 / 人工审核中 / 已判重复）→ duplicate
- 扩展范围到人工审核中：同一文件先传一份进审核、再传一份时，
  直接判重复、不二次识别（省 OCR/AI token），不再只查已归档。
- 用户可选择：保留原文件 / 跳过 / 移动到重复文件夹（由上层处理）
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import (
    Document,
    STATUS_ARCHIVED,
    STATUS_DUPLICATE,
    STATUS_NEED_REVIEW,
)
from backend.utils.hash_utils import sha256_file
from backend.utils.logger import get_logger

logger = get_logger("services.duplicate_service")

# 视为"已见过"的状态：命中任一即判重复（hash 相同 = 内容字节相同）
DUPLICATE_STATUSES = (STATUS_ARCHIVED, STATUS_NEED_REVIEW, STATUS_DUPLICATE)


class DuplicateService:
    def file_hash(self, path) -> str:
        return sha256_file(path)

    def find_duplicate(
        self, db: Session, file_hash: str, exclude_id: int | None = None
    ) -> Document | None:
        """在已定性文档（归档/审核中/已重复）中查找相同 hash 的记录。

        返回最早的一条，作为「重复来源」。取最早便于稳定指向原始文件。
        exclude_id：排除指定文档（人工审核归档时排除自己，避免
        处于 need_review 的文档在归档时命中自身被判重复）。
        """
        q = db.query(Document).filter(
            Document.file_hash == file_hash,
            Document.status.in_(DUPLICATE_STATUSES),
        )
        if exclude_id is not None:
            q = q.filter(Document.id != exclude_id)
        return q.order_by(Document.id.asc()).first()

    def check(self, db: Session, path) -> tuple[bool, Document | None]:
        """检查文件是否重复。返回 (is_duplicate, 已存在文档或 None)。"""
        file_hash = self.file_hash(path)
        dup = self.find_duplicate(db, file_hash)
        if dup is not None:
            logger.info("发现重复文件: %s 与文档#%d 相同", path, dup.id)
            return True, dup
        return False, None


duplicate_service = DuplicateService()
