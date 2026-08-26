# ADR-0011：版本化请求配置与正文附件子证据

## 状态

已接受，随 v1.1.0 交付。

## 背景

普通 HTTP 获取原先只发送 `MetricPulse/1.0` User-Agent，而浏览器降级已使用
真实 Chromium 上下文。来源管线能解析直接指向 PDF/Word 的 URL，但不会发现
HTML 正文里的附件链接，导致目标值仅位于官方附表时不必要地搜索降级。

## 决策

1. 普通 HTTP 使用 `http-desktop-chrome-zh-v1` 请求配置：稳定桌面 UA、Accept、
   Accept-Language 和 Upgrade-Insecure-Requests。不伪造 `Sec-Fetch-*`/`sec-ch-ua`；父页
   Referer 去除查询和 fragment，跨域时仅发送 origin。
2. Playwright 保留实际 Chromium UA 和原生客户端提示，补齐 screen、scale、mobile/touch
   一致性，并标记 `playwright-desktop-zh-v1`。仍不绕过验证码或真人挑战。
3. 仅在 `main/article/[role=main]` 范围发现附件，深度固定为一层。导航、页脚、
   广告中的链接不参与。
4. PDF、DOC/DOCX、XLSX、CSV/TSV、JSON/XML/TXT 和图片使用统一的受限下载、
   SSRF 校验、解析、内容哈希和跨行缓存。XLS、PPT/PPTX 和压缩包首版显式标为
   不支持，保留给人工审核，不执行宏、外部链接或归档内容。
5. 默认限制为每父页 5 个、每单元 8 个、合计 50 MB；超限、下载失败和解析
   失败都作为附件 Evidence 保存，不拖垮已可用的父页。
6. 附件是父来源的子证据，继承 `DIRECT_LINK` 或 `SEARCH_FALLBACK` 路由。当 VERIFY
   引用附件时，正式 `source_url` 写附件 URL，同时在 Evidence/SourceContext 保留父页 URL。
7. 所有父页和成功附件获得描述字段相关的上下文分片；完整解析文本留在缓存和
   确定性结构匹配中。不为每个附件增加模型调用，每单元仍严格以
   `SYNTHESIZE -> VERIFY` 结尾。
8. GitHub 月榜和 Forbes AI 50 固定 Profile 默认禁用附件扩展，避免改变固定榜单口径。

## 审核和安全后果

审核页展示附件和父页两个可点击链接、格式、大小、链接文字与失败原因。
搜索候选仍只供人工调查，不因其附件被发现就自动成为正式来源。每个附件初始 URL
和最终重定向 URL 都经公共地址校验；附件不起新的搜索路由，不改变任务状态机。
