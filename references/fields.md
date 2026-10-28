# 实测字段与判定规则

> 实测时间：2026-10-28，样本 `https://v.douyin.com/aAQgycBvBp0/`（video）
> 本机环境：Python 3.14 / Node 22.19 / Chrome 131

## 一、可用通道（当前唯一可行）

**Chrome CDP 拦截 `aweme/v1/web/aweme/detail`**

页面自己发出的请求由抖音自己的 webmssdk 完成签名，我们只读响应体，不参与签名，因此不受 `a_bogus` / secsdk 变更影响。代价是需要启动一个 headless Chrome（约 4~8 秒）。

已确认失效的通道，别再试：

| 通道 | 结果 |
|---|---|
| `www.iesdouyin.com/web/api/v2/aweme/iteminfo/` | 空 body |
| `iesdouyin.com/share/video/<id>/` SSR 页 | `_ROUTER_DATA.loaderData.video_layout = "None"`，壳页 |
| `www.douyin.com/video/<id>` 裸 HTTP | 只有 JSVMP 壳，无内嵌 JSON |
| `www.douyin.com/aweme/v1/web/aweme/detail/` 纯 HTTP | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| 补 `ttwid` cookie 后重试 | 仍 403，`uifid` 需 secsdk JS 生成，纯 HTTP 造不出 |
| 移动端分享页 | 「抱歉出错了，请尝试在抖音内观看」 |

`aweme.snssdk.com` / `api.amemv.com` 的 App 端 detail 接口返回空 200，需要 X-Gorgon 系签名，未采用。

## 二、字段映射（全部来自实测）

| 输出字段 | 源字段 | 备注 |
|---|---|---|
| `content.desc` | `desc` | 含 `#话题`，展示时剥掉 |
| `content.hashtags` | `text_extra[].hashtag_name` | 去重 |
| `content.publish_time` | `create_time` | 10 位秒级，转 +08:00 |
| `content.ip_location` | **无** | 见第三节 |
| `content.is_ad` | `is_ads` | |
| `content.ai_generated` | `is_aigc_media` | 另可参考 `risk_infos.content` |
| `video.play_url` | `video.play_addr.url_list[0]` | 播放流，通常无水印 |
| `video.download_url` | `video.download_addr.url_list[0]` | URL 内 `watermark=1`，带水印 |
| `video.cover_url` | `video.cover` | 默认封面 |
| `video.origin_cover_url` | `video.origin_cover` | 原始帧，通常 360p |
| `video.dynamic_cover_url` | `video.dynamic_cover` | webp 动图 |
| `video.expires_at` | `video.cdn_url_expired` | Unix 秒，实测约 3 小时后 |
| `audio.play_url` | `music.play_url.url_list[0]` | mp3/m4a |
| `audio.is_original_sound` | `music.is_original_sound` | true = 作者原声 |
| `audio.title` / `author` | `music.title` / `music.author` | |
| `statistics.*` | `statistics.digg_count` 等 | `play_count` 恒 0 |
| `subtitles` | `video.cla_info.caption_infos[]` | 无字幕时整个字段不存在 |
| `images` | `images[].url_list[0]` | 图文作品 |
| `type` | 由 `images` + `video.play_addr` 推断 | 有图无流 → `note` |
| `author.*` | `author.nickname/unique_id/sec_uid/...` | |

## 三、IP 属地：确认不可得

三条独立路径均无该字段：

1. `aweme_detail` 全部 151 个顶层字段，无任何 `ip*` 字段，`region` 为空串；
2. `user/profile/other` 返回的 `user` 131 个字段，只有 `city` / `province`（均为空串）与 `store_region`（商业用地域码，如 `cn-sc`，非用户属地），无 `ip_location`；
3. 移动端分享页直接拒绝访问。

结论：抖音 Web 端不展示也不下发 IP 属地，属 App 端能力。`ip_location` 保留为 `null` 字段以兼容未来接口变化；`warnings` 里会带一条说明，但 Markdown 摘要中不显示，避免每条都刷。

如果后续确需该项，只有两条路：App 端接口（X-Gorgon/X-Khronos 签名体系，维护成本高）或第三方 API。

## 四、字幕来源判定

`video.cla_info.caption_infos[]` 每个轨道的字段（不同版本有差异）：`lang`、`caption_format`、`url`、`url_list`、`sub_id`、`expire` 等。

判定顺序（`scripts/douyin.py` 的 `build_subtitles`）：

1. **显式字段**：存在 `is_author` / `author_upload` → `cc`；存在 `source` / `caption_source` 且值为 `cc|author|user` → `cc`，`asr|auto|machine` → `asr`。
2. **结构特征**：解析出的 JSON 含 `words` / `word_list` / `confidence`（词级时间戳）→ `asr`。
3. 以上都没有 → `unknown`。

`source_basis` 记录当次判定依据（如“词级时间戳/confidence 结构”“接口未给来源字段，仅句子级时间轴”），便于事后核对规则是否还成立。

**注意**：按此规则大部分轨道会落到 `unknown`，这是“宁缺毋滥”的必然结果，不是缺陷。若将来想提高 `cc` 识别率，可加标点规范性/口头语占比等低置信启发式，但必须先用样本验证准确率，且默认关闭。

## 五、多清晰度：`video.bit_rate[]`

实测同一作品会返回 11~19 条 `bit_rate` 项，但**大部分是同一档位的重复变体**（不同码率、h264/h265 各一份），真正的清晰度档位只有 3~4 个。

每条结构：`gear_name`、`bit_rate`、`quality_type`、`FPS`、`is_h265`、`play_addr{width,height,data_size,url_list}`。

去重规则（`scripts/douyin.py` 的 `extract_qualities`）：

1. 按 `min(width, height)` 归到标准档 `1080 / 720 / 540 / 480 / 360 / 240`（竖屏 1080×1920 → 1080P；576×1024 归 540P）；
2. 同档内**优先 `is_h265 == 0`（h264）**，其次取 `bit_rate` 最大者；
3. 按档位从高到低排序。

选 h264 优先是刻意的：h265（bytevc1）体积小但部分播放器/老设备不支持，给用户默认能播的那条更稳妥。`label` 之外还带 `codec` 字段，需要 h265 时可自行取。

`video.play_addr` 是页面默认播放的那条，通常等于最高档；`download_addr` 是带水印的 720p，不作清晰度来源。

## 六、视频直链的 Referer 陷阱（2026-10-28 实测）

`play_addr.url_list` 会同时给 3 条候选：

| 候选 | 形态 | 裸请求（无 Referer） |
|---|---|---|
| `v95-web-sz.douyinvod.com/...` | 裸 CDN | 有时 200，有时 403 |
| `v11-weba.douyinvod.com/...` | 裸 CDN | 同上 |
| `www.douyin.com/aweme/v1/play/?video_id=...` | 302 跳转 | **稳定 200** |

三条拿到的是**同一份文件**（实测均为 2586970B），但裸 CDN 节点的防盗链策略按节点随机：`v26-webf` 这类节点强制要求 `Referer: https://www.douyin.com/`，其余 Referer（含空、`127.0.0.1`、任意站点）一律 403。

这对本技能是致命的——用户点击链接时，浏览器带的 Referer 是 GUI 页面或没有 Referer，**恰好落在被拒的那一类**。所以：

- `choose_url()` 一律**优先 `www.douyin.com/aweme/v1/play/`** 那条；
- 选之前用 `Range: bytes=0-1023` 的**裸请求**探一次（`_probe`，带缓存），失败才退到其他候选；
- 全部候选都探不通时，在 `warnings` 里写明档位，不假装可用。

封面（`p3-pc-sign.douyinpic.com`）和 BGM（`douyinstatic.com`）不校验 Referer，裸请求即可，但仍走同一套探测逻辑兜底。

代价：每档视频 + 三张封面 + BGM 各探一次，约增加 1~2 秒。

## 七、URL 被截断导致的 403

实测（2026-10-28）：只要有任意一个字符被改动，`douyinpic` / `douyinvod` 立即 403。带完整 URL 时，五条链接（视频 / 三档封面 / BGM）在裸请求、仅带 UA、带 Referer 三种情况下**全部 200**，说明这些 CDN 不校验 Referer，只校验 URL 里的 `x-signature` / `l` 参数。

因此：

- 对外展示一律用 `[标签](<完整URL>)`，让链接文本短、目标完整；
- **禁止**用 `...` 省略、禁止手工删参数、禁止让用户从聊天记录里复制被折行的 URL；
- 排查“用户说打不开”时，先本地 GET 一遍确认是不是 URL 被改过，再怀疑签名过期。

`video.cdn_url_expired` 给出的到期时间约 3 小时，过期后需重新解析；封面的 `x-expires` 通常远长于此（本次为 2026-11 与 2036 年）。


## 八、待补实测

当前样本无字幕轨，以下仍未在真实带字幕作品上验证过，遇到带字幕的链接时优先确认并更新本文件：

- `caption_infos` 是否真有 `is_author` / `source` 字段，实际键名是什么；
- 字幕文件的实际格式（VTT / SRT / 自有 JSON），句子级时间轴字段名；
- 图文作品（`aweme_type=68`）的 `images` 结构与 live photo 的 `images[].video`。
