# Xiaomi-OCR-0 接入说明（P0 通道 + P1 兜底）

> 目标：给识别链路补一条"更准但更慢"的本地通道，用于关键单据补抽与低置信度复核；
> 默认引擎仍是 RapidOCR，全量扫描不受影响。

## 1. 结论

可以接入，且已落地为可切换的 OCR 引擎（`OCR_PROVIDER=xiaomi`）。

价值在于两点：
- **扫描件/图片识别率**：真实施工结算单上的金额、合同号、日期、数量全部识别正确，比纯正则锚点稳。
- **为字段抽取（KIE）留口**：模型本身支持"按字段输出 JSON"，后续可替代脆弱正则，属 P1。

代价是速度：本机 CPU（i3-12100，37W）实测 **82.6 秒/页**，不适合全量目录扫描。

## 2. 本机部署事实（实测）

| 项目 | 实测值 |
|---|---|
| 后端 | Ollama（OpenAI 兼容） |
| 端点 | `http://127.0.0.1:11434/v1` |
| 模型 | `longwayxu/xiaomi-ocr-0:bf16` |
| 接口 | `POST /v1/chat/completions`，vision 格式（`image_url` + base64 data URI） |
| 单页耗时 | 82.6 s（127 KB JPG，长边压到 1280 前） |
| token 用量 | prompt 2362 / completion 613 / total 2975 |
| 输出格式 | **OTSL**（`<fcel>`/`<lcel>`/`<ecel>`/`<nl>`），不是 Markdown |

> OTSL 是官方约定的中间格式，官方 MCP 服务才会转成 HTML/Markdown。本项目不做 MCP 依赖，
> 由 `strip_otsl()` 就地还原为"行内 ` | ` 分隔"的纯文本，字段提取足够用。

### 识别质量抽样（`data/inbox/宏达（天柱深法瑞）结算单.jpg-2.jpg`）

| 字段 | 模型输出 | 结论 |
|---|---|---|
| 最终结算金额 | 7,922,962.05 | 正确 |
| 已收货款 | 7,990,545.00 | 正确 |
| 应退/应补 | 67,582.95 | 正确 |
| 合同号 | HSYCXS (DD)-2026-0236 | 正确 |
| 日期 | 2026/6/25 | 正确 |
| 港口磅单数量 | 9,914.99 湿吨 | 正确 |
| 合同单价 / 最终结算单价 | 799.05 / 799.09 | 两字段均输出，未混淆 |

## 3. 代码改动

- `backend/config.py`：新增 `OCR_PROVIDER`、`OCR_BASE_URL`、`OCR_MODEL`、`OCR_TIMEOUT`、
  `OCR_MAX_IMAGE_EDGE`、`OCR_FALLBACK`。
- `backend/services/ocr_service.py`
  - `strip_otsl()`：OTSL → 纯文本（`<fcel>`/`<ecel>` 视为单元格边界，`<lcel>`/`<ucel>` 视为跨列延续）。
  - `XiaomiOCRProvider`：压缩长边（默认 1280）→ PNG → base64 → `/chat/completions`，
    `temperature=0`、`max_tokens=2048`，`HTTPError`/结构异常统一包成 `RuntimeError`。
  - `_PROVIDER_REGISTRY` + `build_provider()`：`rapidocr` / `paddleocr` / `xiaomi`，未知值告警后回退。
  - `OCRService.provider` 由硬编码 `RapidOCRProvider` 改为走工厂（**此前切换注释写了但没实现**）。
  - `OCRService.recognize_image` 增加回退：非 rapidocr 引擎失败且 `OCR_FALLBACK=true` 时，
    用 RapidOCR 重跑一次，避免模型服务故障阻塞整理。
- `backend/tests/test_ocr_provider.py`：14 项，覆盖 OTSL 清洗、工厂选择、HTTP 载荷、
  压缩、HTTP 错误、回退与关闭开关（全部用假 HTTP，不联网）。
- `.env.example`：补充 OCR 段说明。

## 4. 启用方式

`.env` 改三行即可，无需重启代码依赖：

```env
OCR_PROVIDER=xiaomi
OCR_BASE_URL=http://127.0.0.1:11434/v1
OCR_MODEL=longwayxu/xiaomi-ocr-0:bf16
OCR_TIMEOUT=180
OCR_MAX_IMAGE_EDGE=1280
OCR_FALLBACK=true
```

改完重启后端服务生效。想回到原来的快通道，把 `OCR_PROVIDER` 改回 `rapidocr`。

## 5. 已知限制

1. **慢**：CPU BF16 单页 ~80 秒。20 页 PDF 约 27 分钟，不能用于全量扫描。
   若必须提速，可降低 `OCR_MAX_IMAGE_EDGE`（如 960）换精度，或改用 GPU/量化版本。
2. **置信度是固定值**：模型不返回逐块置信度，`XiaomiOCRProvider` 统一记 0.85。
   该值会参与 `CONF_*` 加权，若整表切到 xiaomi，综合置信度会整体上移，需配合阈值观察。
3. **表格不是原生 Markdown**：OTSL 已清洗为 ` | ` 分隔的文本，不做 HTML 表格还原；
   需要严格表格结构时再评估接官方 MCP。
4. **未接官方 MCP**：`ocr_image`/`kie_image`/`ocr_pdf` 的 page/region 切分与 KIE JSON 输出
   都在官方 MCP 内，需额外装 PaddleX + PP-DocLayoutV3，本次未做。

## 6. P1：视觉 KIE 兜底与设置页（已实现）

P0 的 `xiaomi` 通道是"整页 OCR 换引擎"，代价是每页 50~80 秒。P1 把它改成**按需兜底**，
不再影响常规整理速度。

### 6.1 新增文件

`backend/services/ocr_kie_service.py`：
- `FIELD_SCHEMA`：10 个字段（company / seller / date / contract_no / invoice_no / amount /
  quantity / vessel / material / summary），与 `field_extractor`、文件名模板同名。
- `build_prompt()`：把字段清单作为 JSON schema 交给模型，要求只输出 JSON、缺失填 null。
- `parse_json_object()`：容忍 ```json 包裹与前后废话；`_normalize()` 归一日期（`2026/6/25` → `2026-06-25`）、
  去掉"元/吨"单位（金额/数量保留千分位逗号，与既有字段口径一致）。
- `extract()`：失败**不抛异常**，返回带 `error` 的 `KIEResult`——兜底链路不能中断主流程。
- `extract_from_file()`：图片直读；PDF 用 `pymupdf` 渲染首页（dpi=200，关键字段都在首页）。

### 6.2 触发条件（`classifier.py` 的 4.4 块）

只有在 `OCR_KIE_ENABLED=true` **且该文件是扫描件/图片（`parsed.needs_ocr`）** 时才评估，满足其一即触发：
1. 该单据类型的必需字段缺失——优先用识别模板的 `require_fields`，没有模板则看 `amount`；
2. 字段平均置信度 `field_conf < REVIEW_THRESHOLD`（默认 0.60）。

低置信度触发时一次补齐 `amount/contract_no/invoice_no/date/company`，避免逐字段多次调用。

另有一条**空文本抢救**分支：RapidOCR 一个字都没认出来（`text` 为空）时，先用 KIE 抽字段，
把字段拼成 `key | value` 文本喂给规则引擎，命中的字段直接并入结果（供 Excel 匹配与命名），
而不是直接判"未提取到文本"退掉。

### 6.3 合并规则（`_merge_kie_fields`）

- **缺失字段**：补入，置信度 `OCR_KIE_CONF`（默认 0.9），`source="KIE"`。
- **冲突字段**：`amount` / `contract_no` / `invoice_no` 以视觉直读为准**覆盖**并记冲突
  （这三类正则最易错位）；其余字段保留既有值，只记冲突备查。
- 只在触发清单内合并，模型多返回的字段忽略。

### 6.4 设置页

`GET /api/settings` 新增透出：`ocr_provider`、`ocr_base_url`、`ocr_model`、`ocr_timeout`、
`ocr_kie_enabled`、`ocr_fallback`。

`GET /api/settings/ocr/health`：探 `{base}/models`（5s 超时），返回
`reachable` / `model_available` / `models` / `error`；本地引擎（rapidocr/paddleocr）直接视为可用。

`PUT /api/settings` 检测到 OCR 端点/引擎变更时调用 `ocr_service.reset()` 与
`ocr_kie_service.reset()`，下一次识别按新配置重建，无需重启服务。

前端设置页「识别能力」卡片：引擎下拉（rapidocr / paddleocr / xiaomi）+ 连通性检测按钮 +
xiaomi 专属的端点/模型名/超时/KIE 开关/失败回退。

### 6.5 测试

`backend/tests/test_ocr_kie.py`（24 项）：JSON 解析容错、值清洗、prompt、假 HTTP 成功/500/网络错误、
`reset()`、触发判定（缺金额 / 字段齐全 / 低置信度扩字段 / 模板必需字段）、合并规则（补缺失 / 金额覆盖 /
非覆盖字段保留 / 清单外忽略 / 同值不记冲突）。

## 7. 后续（P2）

- **真实单据 A/B**：同一批单据在 rapidocr / xiaomi / rapidocr+KIE 三种组合下各跑一次，
  记录字段命中率与耗时，作为是否扩大兜底范围的依据。
- **多页 PDF**：目前 KIE 只读首页，跨页金额合计（如分页结算单）需要逐页抽再合并。
- **MCP 评估**：若需要严格表格结构（HTML）或 region 切分，再接官方 MCP（需 PaddleX + PP-DocLayoutV3）。

