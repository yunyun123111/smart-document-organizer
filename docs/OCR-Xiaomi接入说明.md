# Xiaomi-OCR-0 接入说明（P0）

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

## 6. 后续（P1 / P2）

- **P1：KIE 字段抽取器**。把每类单据的字段清单（结算金额、已收货款、应退、合同号、发票号、日期）
  作为 schema 直接喂模型，输出 JSON，替代 `field_extractor` 的正则分支；按单据类型按需触发，
  只在"关键单据 + 正则抽空/冲突"时调用，规避 80 秒/页 的成本。
- **P1：按需触发策略**。默认走 RapidOCR；当规则/模板识别置信度低于 `REVIEW_THRESHOLD`
  或关键字段缺失时，再用 xiaomi 复核一次。
- **P2：设置页切换**。`backend/api/settings.py` 目前只透出 `ocr_enabled`，可加 provider 下拉 +
  连通性测试按钮（`GET {base}/models`）。
- **P2：真实单据 A/B**。同一批单据在 rapidocr / xiaomi 下各跑一次，记录字段命中率与耗时，
  作为是否扩大使用范围的依据。
