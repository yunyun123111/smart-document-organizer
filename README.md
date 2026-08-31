# Smart Document Organizer 智能文档自动整理工具

> 本地优先的智能文档自动整理工具：把杂乱文件放入指定目录，系统自动识别、重命名、分类、归档；无法判断的文件交给人工确认。

**核心产品理念**：*让机器处理确定的事情，让人处理机器不确定的事情*——规则引擎优先，AI 只兜底，置信度分档，人工审核兜底，全程操作日志与撤销，绝不误删文件。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+ / FastAPI / SQLAlchemy / SQLite / Pydantic |
| 前端 | Vue 3 / Vite / TypeScript / Element Plus / Pinia |
| 文件解析 | PyMuPDF / python-docx / openpyxl / Pillow |
| OCR | RapidOCR（PP-OCR 系模型 / ONNX 本地运行；Provider 抽象可替换 PaddleOCR） |
| AI | OpenAI Compatible API（OpenAI / DeepSeek / Qwen / Ollama，Provider 抽象，未配置自动降级纯规则） |

## 目录结构

```
smart-document-organizer/
├── backend/            # FastAPI 后端
│   ├── api/            # REST API 路由
│   ├── models/         # SQLAlchemy 模型（7 张表）
│   ├── schemas/        # Pydantic 请求/响应模型
│   ├── services/       # 业务逻辑（解析/规则/AI/归档等）
│   ├── ai/             # AI Provider 抽象与实现
│   └── utils/          # 工具（日志/文件/哈希/文件名）
├── frontend/           # Vue 3 前端
├── data/               # 运行数据（数据库/日志/临时文件）
├── tests/              # 集成测试
└── docs/               # 设计文档
```

## 快速开始

### 后端

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. 配置环境变量（可选，默认即可运行）
copy .env.example .env          # Windows

# 3. 启动后端（首次启动自动建表 + 写入默认分类）
python -m uvicorn backend.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173 （/api 自动代理到后端 8000）
```

## 测试

```bash
python -m pytest backend/tests -v
```

## 开发进度

按规格书 Phase 1 → 17 分三个里程碑推进：

- **里程碑 1（Phase 1–3）**：项目骨架 / 数据库（7 表 + 18 默认分类）/ 文件解析 ✅
- **里程碑 2（Phase 4–10）**：OCR / 规则引擎 / 字段提取 / AI / 置信度 / 重命名 / 归档（含去重与撤销）✅
- **里程碑 3（Phase 11–17）**：批量整理 / 人工审核 / 操作日志 / 文档库 / 分类与规则管理 / 系统设置 / 数据看板 / 完整测试 ✅

**测试**：后端 126 项测试全部通过（解析 / OCR / 规则 / 字段 / AI / 置信度 / 重命名 / 归档 / 分类引擎 / API 集成）。

**快速使用**：`start.bat` 一键启动（后端 8000 + 前端 5173），浏览器打开 <http://127.0.0.1:5173>。

详细规格见 `PROJECT_SPEC.md`。
