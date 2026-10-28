# 抖音解析 · douyin-parser

> 给 AI Agent 用的抖音作品信息解析技能：**只读、不下载**。
> 输入一段分享文案 / 短链 / 作品链接，输出一张结构化的作品信息表。

## 为什么不是又一个解析站

绝大多数在线「去水印解析」都在做同一件事：把分享短链换成视频 ID，再调抖音自己的接口拿无水印直链。难点从来不在接口，而在**签名墙**——`a_bogus`、`msToken`、以及 2026 年的 secsdk 三件套（`timestamp` + `x-secsdk-web-signature` + `uifid`），纯 HTTP 请求会被直接拦掉：

```
$ curl 'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=...'
403 Blocked by ArgusSecurityPlugin Uifid Not Found
```

本项目**不碰签名**。它用本机 Chrome 通过 CDP 打开作品页，拦截页面自己发出的那个 `detail` 请求——签名由抖音的前端代码自己完成，我们只读响应。签名算法怎么改都与我无关。

代价是需要一个 headless Chrome（约 4~8 秒 / 次），换来的是长期可用性。

## 能拿到什么

| 项目 | 内容 |
| --- | --- |
| 👤 作者 | 昵称、抖音号、`sec_uid`、简介、粉丝数 |
| 🕒 发布 | 精确到秒的发布时间 |
| 📝 文案 | 正文（自动剥掉话题标签） |
| 🏷️ 标签 | 去重后的话题列表 |
| 📊 数据 | 赞 / 评 / 藏 / 转 |
| 📐 规格 | 时长、分辨率、是否广告、是否 AI 生成 |
| 🎬 视频 | **多档清晰度**直链（实测 540P / 720P / 1080P），各带体积 |
| 🖼️ 封面 | 默认 / 原始帧 / 动态（webp）三档 |
| 🎵 BGM | 原声直链 + 是否作者原声 |
| 💬 字幕 | CC / ASR 字幕全文，带时间轴；判不出来源时标 `unknown` |

顺便说几个**拿不到**的，省得踩坑：IP 属地（Web 接口不下发）、精确播放量、拍摄 EXIF、评论列表、用户主页作品。

## 安装

环境要求：Windows、Python 3.9+、Node 22+、Chrome。

```powershell
git clone https://github.com/Richard2091/douyin-parser.git "$env:USERPROFILE\.agents\skills\douyin-parser"
py -3 -X utf8 "$env:USERPROFILE\.agents\skills\douyin-parser\scripts\doctor.py"
```

`doctor.py` 会检查 Python / requests / Node 版本 / Chrome 路径，缺什么直接说。

## 用法

```powershell
# 直接贴分享文案，脚本自己抽链接（口令、Emoji、乱码都无所谓）
py -3 -X utf8 scripts\douyin.py "4.89 复制打开抖音，看看【xxx的作品】... https://v.douyin.com/xxxx/ ..."

# 机器可读
py -3 -X utf8 scripts\douyin.py --json "https://v.douyin.com/xxxx/"

# 批量（空格分隔或从 stdin）
py -3 -X utf8 scripts\douyin.py "<link1>" "<link2>"
```

输出示例：

```
| 项目 | 内容 |
| --- | --- |
| 👤 作者 | **EVERGREEN.**（抖音号 `evergreensunny`） |
| 🕒 发布 | 2026-10-22 12:24 |
| 📝 文案 | 你倒是上去啊 |
| 🏷️ 标签 | `#这车技` `#大学生专注力差怎么补救` |
| 📊 数据 | 👍 40642 · 🗨️ 1428 · ⭐ 1716 · 🔁 18836 |
| 📐 规格 | ⏱ 0:10 · 1080×1920 |
| 🎬 视频 | [1080P·2.5M](...) · [720P·1.1M](...) · [540P·1.7M](...)　⏳ 18:44 到期 |
| 🖼️ 封面 | [默认](...) · [原始帧](...) · [动态](...) |
| 🎵 BGM | 原声 `@xxx创作的原声` — xxx（0:10） [播放](...) |
| 💬 字幕 | 无 CC/ASR 字幕 |
```

## 两个已经踩过的坑

**1. 直链会 403，而且不一定怪你。** CDN 节点的防盗链策略按节点随机：有的节点裸请求就能下，有的强制要求 `Referer: https://www.douyin.com/`，其余一律 403。而用户点击链接时，浏览器带的 Referer 是当前页面，恰好落在被拒的那一类。

所以脚本**优先选 `www.douyin.com/aweme/v1/play/` 那条 302 跳转地址**（实测与裸 CDN 拿到的是同一份文件，但跳转每次都落到健康节点、不校验 Referer），并且在发出前用 `Range: bytes=0-1023` 的裸请求逐条探测，探不通才退到备选。

**2. 链接一个字符都不能少。** 这些 URL 带 `x-signature` / `sign` / `l` 等签名参数，截断或用 `...` 省略就是 403。

## 目录结构

```
douyin-parser/
├── SKILL.md               技能说明（触发词、契约、失败处置）
├── scripts/
│   ├── doctor.py          环境自检
│   ├── douyin.py          主入口：抽链 → 解析 → 渲染
│   └── capture.mjs        Chrome CDP 引擎（零依赖，Node 内置 WebSocket）
└── references/
    └── fields.md          实测字段表、清晰度去重、Referer 陷阱、失效通道记录
```

## 已知边界

- **只读**：不下载、不落盘。要文件请点直链，或用浏览器自带另存。
- **不做 ASR 转写**：只取平台已有的 CC/ASR 字幕；视频没有字幕轨时返回空。
- **不支持**：用户主页作品列表、评论、搜索、直播、合集——这些是另一套接口与签名。
- **Window 专场**：Chrome 路径默认 `C:\Program Files\Google\Chrome\Application\chrome.exe`，其他路径用 `CHROME_PATH` 环境变量覆盖。Linux/macOS 未测试。

## License

MIT
