"""models 包统一导出，方便其它模块 `from backend.models import Document`。"""
from backend.models.base import Base, TimestampMixin
from backend.models.category import Category
from backend.models.document import (
    ALL_STATUSES,
    STATUS_ARCHIVED,
    STATUS_DUPLICATE,
    STATUS_FAILED,
    STATUS_NEED_REVIEW,
    STATUS_PENDING,
    STATUS_PROCESSED,
    STATUS_PROCESSING,
    STATUS_SKIPPED,
    Document,
)
from backend.models.filename_rule import FilenameRule
from backend.models.document_field import (
    ALL_SOURCES,
    SOURCE_AI,
    SOURCE_OCR,
    SOURCE_RULE,
    SOURCE_USER,
    DocumentField,
)
from backend.models.operation_log import (
    ALL_OPERATION_TYPES,
    OP_ARCHIVE,
    OP_CLASSIFY,
    OP_DELETE,
    OP_IMPORT,
    OP_MOVE,
    OP_OCR,
    OP_PARSE,
    OP_RENAME,
    OP_UNDO,
    OP_USER_EDIT,
    RESULT_FAILED,
    RESULT_OK,
    OperationLog,
)
from backend.models.processing_job import (
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_RUNNING,
    ProcessingJob,
)
from backend.models.rename_template import RenameTemplate
from backend.models.recognition_template import RecognitionTemplate
from backend.models.document_sample import DocumentSample
from backend.models.rule import ALL_MATCH_TYPES, MATCH_CONTAINS, MATCH_EXACT, MATCH_REGEX, Rule

__all__ = [
    "Base",
    "TimestampMixin",
    "Document",
    "DocumentField",
    "Category",
    "Rule",
    "FilenameRule",
    "RenameTemplate",
    "RecognitionTemplate",
    "DocumentSample",
    "ProcessingJob",
    "OperationLog",
    # 状态 / 来源 / 操作类型 / 匹配类型
    "ALL_STATUSES",
    "STATUS_PENDING",
    "STATUS_PROCESSING",
    "STATUS_PROCESSED",
    "STATUS_NEED_REVIEW",
    "STATUS_FAILED",
    "STATUS_ARCHIVED",
    "STATUS_DUPLICATE",
    "STATUS_SKIPPED",
    "ALL_SOURCES",
    "SOURCE_RULE",
    "SOURCE_OCR",
    "SOURCE_AI",
    "SOURCE_USER",
    "ALL_OPERATION_TYPES",
    "OP_IMPORT",
    "OP_PARSE",
    "OP_OCR",
    "OP_CLASSIFY",
    "OP_RENAME",
    "OP_MOVE",
    "OP_ARCHIVE",
    "OP_USER_EDIT",
    "OP_DELETE",
    "OP_UNDO",
    "RESULT_OK",
    "RESULT_FAILED",
    "JOB_RUNNING",
    "JOB_COMPLETED",
    "JOB_CANCELLED",
    "JOB_FAILED",
    "ALL_MATCH_TYPES",
    "MATCH_CONTAINS",
    "MATCH_EXACT",
    "MATCH_REGEX",
]
