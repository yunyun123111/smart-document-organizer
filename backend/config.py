"""应用配置系统。

使用 pydantic-settings 从 .env / 环境变量加载配置。
所有路径统一解析为绝对路径，避免工作目录变化导致的问题。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录 = 本文件的上两级目录（backend/config.py -> 项目根）
# 打包成 exe 后，这里就是 exe 所在目录（PyInstaller onedir）
BASE_DIR = Path(__file__).resolve().parent.parent

# 是否以打包形态运行（PyInstaller 会在启动时注入 sys.frozen）
IS_FROZEN = bool(getattr(sys, "frozen", False))

# 用户可见的目录：打包后是 exe 所在目录（portable.flag 放这里），
# 源码运行时与 BASE_DIR 相同。
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else BASE_DIR


def _resolve_data_home() -> Path:
    """运行数据根目录（数据库、文档、日志、配置）。

    源码运行：项目根 data/ —— 保持开发与调试习惯不变。
    安装形态：%APPDATA%/SmartDocOrganizer —— Program Files 无写权限，
              且覆盖安装会清掉程序目录，数据必须放在用户目录。
    绿色便携：程序目录放一个 portable.flag，数据留在程序目录。

    SDO_DATA_HOME 环境变量优先级最高，用于部署机、测试或迁移时临时指定。
    """
    raw = os.environ.get("SDO_DATA_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if (APP_DIR / "portable.flag").exists():
        return (APP_DIR / "data").resolve()
    if IS_FROZEN:
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return (Path(appdata) / "SmartDocOrganizer").resolve()
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            return (Path(local) / "SmartDocOrganizer").resolve()
    return (BASE_DIR / "data").resolve()


DATA_HOME = _resolve_data_home()

# 扩展包目录：安装形态下，视觉扩展包（OCR + OpenCV）下载后解压到这里，
# 每个子目录是一个扩展包（当前只有 vision）。
EXTENSIONS_DIR = DATA_HOME / "extensions"


def _activate_extensions() -> list[str]:
    """把已安装的扩展包挂到 sys.path，使其中的第三方包可被 import。

    主安装包不含 cv2 / rapidocr / onnxruntime / numpy，它们由设置页下载的
    扩展包提供。PyInstaller 冻结环境下 sys.path 只有 _internal，因此必须在导入
    任何业务模块之前显式插入扩展目录；.pyd 依赖同目录 DLL，还要注册 DLL 目录。
    """
    activated: list[str] = []
    if not EXTENSIONS_DIR.is_dir():
        return activated
    for pkg_dir in sorted(EXTENSIONS_DIR.iterdir()):
        if not pkg_dir.is_dir() or pkg_dir.name.startswith("."):
            continue
        path = str(pkg_dir)
        if path not in sys.path:
            sys.path.insert(0, path)
        activated.append(pkg_dir.name)
        if os.name == "nt":
            dll_dirs = {path}
            for dll in pkg_dir.rglob("*.dll"):
                dll_dirs.add(str(dll.parent))
            for d in dll_dirs:
                try:
                    os.add_dll_directory(d)
                except OSError:  # 目录不存在或无权限，忽略
                    pass
    return activated


ACTIVATED_EXTENSIONS = _activate_extensions()


def _resolve_env_file() -> Path:
    """配置文件位置。

    源码运行沿用项目根 .env（现有部署脚本与文档都依赖它）。
    安装/便携形态写到数据目录下的 config.env，避免写程序目录失败。
    """
    if IS_FROZEN or (APP_DIR / "portable.flag").exists():
        return DATA_HOME / "config.env"
    return BASE_DIR / ".env"


# 全进程唯一的配置文件路径：Settings 读取与 update() 回写都用它
ENV_FILE = _resolve_env_file()


def _resolve_dir(raw: str) -> Path:
    """把配置里的目录解析成绝对路径。

    .env 模板默认写 ./data/documents 这种相对路径，一旦工作目录变了（计划任务、
    双击 bat、注册表自启），相对路径就会指向别处，表现为"文档全部不识别"。
    统一按项目根解析，保证任何启动方式下路径都稳定。
    """
    p = Path(str(raw)).expanduser()
    if p.is_absolute():
        return p
    return ((DATA_HOME if IS_FROZEN else BASE_DIR) / p).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用基础
    APP_NAME: str = "SmartDocumentOrganizer"
    APP_ENV: str = "development"
    DEBUG: bool = True

    # 数据库
    DATABASE_URL: str = f"sqlite:///{DATA_HOME / 'database/app.db'}"

    # 目录
    DOCUMENT_ROOT: str = str(DATA_HOME / "documents")
    INBOX_ROOT: str = str(DATA_HOME / "inbox")
    TEMP_DIR: str = str(DATA_HOME / "temp")
    RECYCLE_BIN_ROOT: str = str(DATA_HOME / "recycle_bin")
    # 归档目录结构模板（/ 分隔；支持 category/year/month/day/date 及任意业务字段名，
    # 如 customer/counterparty/contract_no/project_no/ship_name；字段缺失时该段自动跳过）
    ARCHIVE_TEMPLATE: str = "category/year/month"
    EXCEL_SOURCES_ROOT: str = str(DATA_HOME / "excel_sources")

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = str(DATA_HOME / "logs")

    # 文件操作
    ALLOW_OVERWRITE: bool = False

    # 局域网访问
    HOST_BIND: str = "127.0.0.1"  # 127.0.0.1 仅本机；0.0.0.0 开放局域网访问
    ACCESS_PASSWORD: str = ""  # 访问密码（非空时启用登录校验；开放局域网建议设置）

    # 邮箱接收（手机发邮件附件 -> 自动识别归档）
    EMAIL_ENABLED: bool = False
    EMAIL_IMAP_HOST: str = ""
    EMAIL_IMAP_PORT: int = 993
    EMAIL_USER: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_POLL_INTERVAL: int = 120  # 秒
    EMAIL_SSL: bool = True  # IMAP over SSL（993 端口）

    # 目录自动监控（主动式：设置目录后自动扫描触发整理）
    WATCH_ENABLED: bool = False
    # 监控目录列表，JSON 数组字符串（如 '["D:\\共享\\a","D:\\共享\\b"]'）
    WATCH_DIRS: str = "[]"
    WATCH_INTERVAL: int = 30  # 秒

    # 盖章检测（本地图像算法，零 AI token）
    SEAL_DETECT_ENABLED: bool = True

    # OCR
    OCR_ENABLED: bool = True
    # 识别引擎：rapidocr（默认，快，全量用）| paddleocr | xiaomi（Xiaomi-OCR-0，慢但更准）
    OCR_PROVIDER: str = "rapidocr"
    # Xiaomi-OCR-0 的 OpenAI 兼容推理端点（Ollama: :11434/v1；vLLM/SGLang: :8000/v1）
    OCR_BASE_URL: str = "http://127.0.0.1:11434/v1"
    OCR_MODEL: str = "longwayxu/xiaomi-ocr-0:bf16"
    OCR_TIMEOUT: int = 180  # 秒；CPU 上单页约 80s，留足余量
    OCR_MAX_IMAGE_EDGE: int = 1280  # 送入模型前长边压缩，减少视觉 token
    OCR_FALLBACK: bool = True  # xiaomi 调用失败时回退 rapidocr，避免阻塞整理
    # 视觉 KIE 兜底：扫描件/图片缺关键字段时，让 Xiaomi-OCR-0 直读原图输出 JSON 字段
    OCR_KIE_ENABLED: bool = True
    OCR_KIE_CONF: float = 0.9  # KIE 字段置信度（视觉直读，高于正则的 0.6~0.85）

    # AI（OpenAI Compatible API）
    AI_ENABLED: bool = True
    AI_PROVIDER: str = "openai"  # openai | deepseek | qwen | ollama | custom
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = ""
    AI_TIMEOUT: int = 60
    AI_MAX_RETRIES: int = 2
    AI_MAX_TEXT_LENGTH: int = 1500

    # 落库的正文上限（用于全文检索）。单据类一页约 300~800 字，
    # 20000 字足以覆盖多页合同，同时避免索引过大。
    EXTRACTED_TEXT_LIMIT: int = 20000

    # 置信度阈值
    AUTO_ARCHIVE_THRESHOLD: float = 0.85
    REVIEW_THRESHOLD: float = 0.60

    # 人工确认归档时，把该文档沉淀为格式样本（同类型+同分类已有样本则跳过）
    LEARN_SAMPLE_ON_APPROVE: bool = True

    # 置信度权重
    CONF_RULE_WEIGHT: float = 0.40
    CONF_FIELD_WEIGHT: float = 0.20
    CONF_KEYWORD_WEIGHT: float = 0.15
    CONF_AI_WEIGHT: float = 0.25

    # ---- 派生属性 ----
    @property
    def document_root(self) -> Path:
        return _resolve_dir(self.DOCUMENT_ROOT)

    @property
    def inbox_root(self) -> Path:
        return _resolve_dir(self.INBOX_ROOT)

    @property
    def temp_dir(self) -> Path:
        return _resolve_dir(self.TEMP_DIR)

    @property
    def recycle_bin_root(self) -> Path:
        return _resolve_dir(self.RECYCLE_BIN_ROOT)

    @property
    def excel_sources_root(self) -> Path:
        return _resolve_dir(self.EXCEL_SOURCES_ROOT)

    @property
    def watch_dirs(self) -> list[Path]:
        """解析 WATCH_DIRS JSON 数组为目录列表（跳过非法项）。"""
        import json

        raw = self.WATCH_DIRS or "[]"
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            items = []
        out: list[Path] = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, str) and it.strip():
                    out.append(Path(it.strip()))
        return out

    @property
    def log_dir(self) -> Path:
        return _resolve_dir(self.LOG_DIR)

    def ensure_dirs(self) -> None:
        """确保所有运行所需目录存在（支持 Windows 长路径）。"""
        from backend.utils.fs_path import fs_mkdir

        for p in (
            self.document_root,
            self.inbox_root,
            self.temp_dir,
            self.recycle_bin_root,
            self.excel_sources_root,
            self.log_dir,
            Path(self.DATABASE_URL.replace("sqlite:///", "")).parent
            if self.DATABASE_URL.startswith("sqlite")
            else DATA_HOME / "database",
        ):
            fs_mkdir(p)

    def confidence_weights(self) -> dict[str, float]:
        """返回置信度权重字典（规则/字段/关键词/AI）。"""
        return {
            "rule": self.CONF_RULE_WEIGHT,
            "field": self.CONF_FIELD_WEIGHT,
            "keyword": self.CONF_KEYWORD_WEIGHT,
            "ai": self.CONF_AI_WEIGHT,
        }

    # 供 update() 使用的小写别名 -> 真实配置字段名
    _FIELD_ALIASES = {
        "document_root": "DOCUMENT_ROOT",
        "inbox_root": "INBOX_ROOT",
        "temp_dir": "TEMP_DIR",
        "log_dir": "LOG_DIR",
        "recycle_bin_root": "RECYCLE_BIN_ROOT",
        "archive_template": "ARCHIVE_TEMPLATE",
        "excel_sources_root": "EXCEL_SOURCES_ROOT",
        "watch_enabled": "WATCH_ENABLED",
        "watch_dirs": "WATCH_DIRS",
        "watch_interval": "WATCH_INTERVAL",
        "seal_detect_enabled": "SEAL_DETECT_ENABLED",
    }

    def _resolve_field(self, key: str) -> str:
        """把调用方传入的字段名解析为模型真实字段名（支持小写别名）。"""
        if key in self._FIELD_ALIASES:
            return self._FIELD_ALIASES[key]
        for field in type(self).model_fields:
            if field == key or field.lower() == key.lower():
                return field
        return key

    def update(self, **kwargs) -> None:
        """更新内存配置并持久化到 .env（None 值跳过）。

        只持久化本次显式传入的字段，避免把环境变量/测试注入值泄漏到 .env。
        """
        changed: list[str] = []
        for key, value in kwargs.items():
            if value is None:
                continue
            field = self._resolve_field(key)
            if hasattr(self, field) and getattr(self, field) != value:
                setattr(self, field, value)
                changed.append(field)
        if changed:
            self.ensure_dirs()
            self._persist(changed)

    def _persist(self, fields: list[str] | None = None) -> None:
        """把指定字段写回配置文件（保留已有注释与未管理项）。"""
        env_path = ENV_FILE
        data = {f: getattr(self, f) for f in fields} if fields else self.model_dump()
        lines: list[str] = []
        written = set()
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key = line.split("=", 1)[0].strip()
                    if key in data:
                        lines.append(f"{key}={data[key]}")
                        written.add(key)
                        continue
                lines.append(line)
        for key, value in data.items():
            if key not in written:
                lines.append(f"{key}={value}")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logging.getLogger("config").info("配置已持久化到 %s", env_path)


settings = Settings()
