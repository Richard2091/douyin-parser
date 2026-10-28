---
name: douyin-parser
description: 抖音解析 —— 输入抖音分享文案、v.douyin.com 短链或作品链接，输出作品信息表（文案、话题、发布时间、多清晰度视频直链、多尺寸封面、BGM 原声直链、CC/ASR 字幕全文、互动数据），只读不下载。用户发来抖音分享内容，或要求查看某条抖音视频的文案、封面、背景音乐、字幕、发布时间时使用。
---

# 抖音解析

中文名「抖音解析」，程序标识 `douyin-parser`，调用标识 `$douyin-parser`。**只读技能**：解析出信息就结束，不下载、不落盘、不转码。

实现上不逆向抖音的 `a_bogus` / secsdk 签名，而是用本机 Chrome 通过 CDP 打开作品页、拦截页面自身发出的 `aweme/v1/web/aweme/detail` 响应——签名由抖音自己的前端完成，我们只读结果，因此不受签名算法更新影响。

## 何时用

用户发来抖音分享文案 / 短链 / 作品链接，或问「这条视频的文案是什么」「封面给我」「背景音乐叫什么」「字幕提取一下」「什么时候发的」。用户只是想**看一眼信息**，不是要文件；要文件时说明本技能不下载，把直链给他即可。

## 执行步骤

1. **先自检**：在技能目录运行
   `py -3 -X utf8 scripts/doctor.py`
   缺项照提示补，别硬跑。Node 必须在 PATH（capture.mjs 用 Node 22+ 的 WebSocket）。

2. **直接跑主入口**，把用户原文整段传进去，不要自己先抽链接——脚本内的正则比手抄可靠，分享文案里的口令、Emoji、时间戳全是噪音：
   `py -3 -X utf8 scripts/douyin.py "<用户原文>"`
   多条链接用空格分隔，或写进文件后 `-- < file.txt`。

3. **原样输出脚本的 Markdown 结果**。不要改写、不要总结成一段话、不要省略直链——用户要的就是这些字段。需要额外解读（比如概括文案内容）时放在结果之后另起一段。

4. 需要机器可读时加 `--json`，输出单一对象（单链接）或数组（多链接）。

## 输出契约

Markdown 输出为**两列表格**（左「项目」右「内容」，每行前置 emoji），行序固定：作者 → 发布 → 文案 → 标签 → 数据 → 规格 → 视频 → 封面 → BGM → 字幕。标签用行内代码 `` `#话题` `` 渲染，其余为纯文本。发布时间那一行的标签就叫「发布」，不要写成「发布时间」。

数据行每个指标各带一个 emoji：`👍 赞 · 🗨️ 评论 · ⭐ 收藏 · 🔁 转发`；规格行用 `⏱ 时长 · 宽×高`。emoji 用于快速扫读，不要省略或替换成文字。

视频给**多档清晰度**（实测 540P/720P/1080P 各一条），标签写作 `[1080P·2.5M]` 形式，用户按需点；同一档内优先 h264，避免用户设备不支持 h265 打不开。

**媒体直链一律用 markdown 链接 `[标签](URL)` 渲染，绝不把原始 URL 平铺出来，也绝不截断**。这些 URL 带 `x-signature` / `l` 等签名参数，少一个字符就 403；用 `...` 省略、或手工改写参数，都会让用户点开报错。用最通用的 `(URL)` 形式，**不要**写成 `(<URL>)`——后者部分渲染器不认，会退化成死文本。

**响应里的链接必须贴完整原文，不要在回复里用 `…` 缩写**。脚本已经保证选中的是可以裸请求打开的地址，答复者再缩写就等于把链接弄坏了；嫌版面长就少说两句，不要动 URL。

`--json` 结构固定，`schema_version: 1`，字段只增不改：

```json
{ "schema_version": 1, "ok": true, "aweme_id": "...", "type": "video|note",
  "author": { "nickname", "douyin_id", "sec_uid", "signature", "followers" },
  "content": { "desc", "hashtags", "publish_time", "publish_ts",
               "ip_location", "is_ad", "ai_generated" },
  "video": { "duration_ms", "width", "height", "play_url", "download_url",
             "cover_url", "origin_cover_url", "dynamic_cover_url", "expires_at",
             "qualities": [ { "label": "1080P", "width", "height", "fps",
                              "bitrate", "size", "codec": "h264|h265", "url" } ] },
  "audio": { "type": "bgm", "title", "author", "duration_ms", "play_url",
             "is_original_sound" },
  "images": null, "subtitles": [ { "lang", "source", "source_basis",
                                   "count", "items": [{"start_ms","end_ms","text"}] } ],
  "statistics": { "digg", "comment", "share", "collect" },
  "links": { "video", "cover", "origin_cover", "dynamic_cover", "bgm", "images" },
  "warnings": [] }
```

## 必须保持的行为

- **不下载**。哪怕用户说「存下来」，也只给直链并说明有效期；要文件由用户自己点。这条是技能边界，不要临时加下载逻辑。
- **不猜**。抽不到链接就追问原话，不要凭昵称或关键词去搜。
- **字幕来源按实测判**，判不出写 `unknown`，不硬标 cc/asr；对话里 `unknown` 不展示标签。
- **直链会过期**，输出里必须带上 `⏳ 直链到期`；用户隔一小时再回来用同一条链接要重新跑。
- 用户说「转成文字 / 总结内容」时明确：只取平台已有的 CC/ASR 字幕，**不做 ASR 转写**。
- 批量超过 3 条时先给汇总（标题/时间/有无字幕），详情按需再展开，避免刷屏。

## 失败处置

| 现象 / code | 处理 |
|---|---|
| `BAD_INPUT` | 追问用户原文，不重试 |
| `RESOLVE_FAILED` | 短链失效或不是作品页，请用户重发 |
| `PRIVATE_OR_DELETED` | 直接告知不可见，**不换通道重试**（省时间也避免风控） |
| `RISK_BLOCKED` | 页面弹验证/登录墙。让用户在本机 Chrome 里正常登录一次抖音后重试 |
| `NO_DATA` | 作品可能已删除或仅 App 可见 |
| `NO_BROWSER` | Chrome 路径不对，设 `CHROME_PATH` 环境变量 |

## 已知边界

- **IP 属地不输出**：抖音 Web 接口不下发该字段（detail 151 个字段、user profile 131 个字段都没有），只有 App 端显示。JSON 里 `ip_location` 恒为 `null`，表格中不再出现该行——不要为它加占位行或解释性文字。
- **字幕**：多数作品没有字幕轨；有轨时判断来源依赖 `cla_info.caption_infos` 的字段，判不出就是 `unknown`。
- **播放量**：Web 端恒为 0，不输出。
- 不支持：用户主页作品列表、评论、搜索、直播、合集。这些是另一套接口与签名，需要时另建技能。

## 按需加载

| 当前需要 | 读取内容 |
|---|---|
| 字段含义、字幕来源判定规则、通道失效记录 | [实测字段与判定](references/fields.md) |
