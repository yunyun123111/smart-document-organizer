# 项目规格（PROJECT_SPEC）

本文档是 `metool_markdown_edit.md`（智能文档自动整理工具 V1.0 项目规格书）的落地摘要。
完整规格以原始开发文档为准，开发严格按规格书 Phase 1 → 17 执行。

## 一、项目定位

- 本地优先的个人/小型团队文档整理工具
- 核心流程：文件发现 → 类型检测 → 文本提取/OCR → 文本清洗 → 关键词识别 → 规则引擎 → 字段提取 → AI 辅助 → 置信度 → 自动归档/人工确认 → 重命名 → 分类 → 移动归档 → 记录日志
- V1 不做：微服务 / K8s / 多租户 / SSO / 在线协作 / 云端同步 / 手机端 / 向量库 / RAG

## 二、支持文件类型

- V1：PDF / JPG / JPEG / PNG / DOC / DOCX / XLS / XLSX / TXT / CSV
- 重点保证：PDF / JPG / PNG / DOCX / XLSX
- V2 候选：PPT / PPTX / WEBP / RAR / ZIP / EML / MSG / MD / HTML

## 三、运行模式

- 模式一（手动整理）：选择文件/文件夹 → 批量处理
- 模式二（自动监控，V2）：监控 `D:\待整理`，文件稳定后自动处理

## 四、文件安全（最高优先级）

- 禁止未经确认删除原文件；默认移动而非复制+删除
- 所有文件操作记录日志（原/新文件名、原/新路径、时间、类型、结果）
- 支持撤销：根据操作日志 new_path → old_path 恢复
- SHA256 去重；同名禁止覆盖（除非显式允许）

## 五、架构

```
Vue 3 UI → FastAPI → {文件解析, OCR服务, AI服务} → 文本处理 → 规则引擎
         → 分类引擎 → 字段提取 → 置信度计算 → 自动归档 | 人工审核 → 文件管理器 → SQLite+FS
```

## 六、数据库（7 表）

documents / document_fields / categories / rules / rename_templates / processing_jobs / operation_logs

- 文件状态机：pending → processing → parsed → classified → archived | need_review → approved → archived；failed；duplicate

## 七、置信度系统

```
最终置信度 = 规则×40% + 字段×20% + 关键词×15% + AI×25%（权重可配置）
≥0.85 自动归档 | 0.60–0.85 人工确认 | <0.60 无法判断
```

## 八、AI / OCR 可替换

- 业务代码只调用 AIService / OCRService，不得散落 SDK 调用
- AI 返回必须 JSON 且验证，失败自动重试，再失败进人工审核
- 长文本不整篇发送 AI：预处理 + 截断 + 摘要 + 关键词

## 九、开发阶段（Phase 1–17）

| 阶段 | 内容 | 里程碑 |
|---|---|---|
| 1 项目初始化 | 环境/骨架/配置/日志 | M1 |
| 2 数据库 | 7 表 + 初始化 | M1 |
| 3 文件解析 | 统一接口 + 7 解析器 | M1 |
| 4 OCR | OCRService + PaddleOCRProvider | M2 |
| 5 规则引擎 | 关键词/正则/优先级/权重 | M2 |
| 6 字段提取 | 公司/日期/编号/金额 | M2 |
| 7 AI | Provider + JSON 校验/重试/超时 | M2 |
| 8 置信度 | ConfidenceService | M2 |
| 9 重命名 | 模板/非法字符/重名 | M2 |
| 10 归档 | 建目录/移动/日志/去重 | M2 |
| 11 人工审核 | Review API + UI | M3 |
| 12 操作日志 | 记录/错误/撤销 | M3 |
| 13 批量整理 | 任务/进度/队列 | M3 |
| 14 Dashboard | 首页统计 | M3 |
| 15 分类管理 | 分类/关键词/规则/模板 UI | M3 |
| 16 系统设置 | 目录/OCR/AI/阈值/模板 | M3 |
| 17 完整测试 | 全量回归 | M3 |

## 十、V1 验收标准（摘要）

PDF 识别 / 扫描 PDF OCR / JPG/PNG OCR / Word 解析 / Excel 解析 / 批量处理；
文档类型、公司、日期、编号、金额、关键词识别；AI 辅助；自定义分类与规则；置信度；自动归档 + 人工确认；
重命名模板/动态字段/非法字符/重名处理；SHA256；操作日志；撤销；错误处理；不误删文件。
