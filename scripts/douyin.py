#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音作品信息查询（只读，不下载）。

用法:
    py -3 -X utf8 douyin.py "<分享文案或链接>" [...更多链接]
    py -3 -X utf8 douyin.py --json "<链接>"
    type share.txt | py -3 -X utf8 douyin.py -

输出: 默认 Markdown 摘要；--json 输出结构化 JSON。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("缺少依赖: pip install requests")

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "capture.mjs")
TZ = timezone(timedelta(hours=8))

URL_RE = re.compile(
    r"https?://(?:v\.douyin\.com/[\w-]+"
    r"|(?:www\.)?douyin\.com/(?:video|note)/\d+"
    r"|(?:www\.)?iesdouyin\.com/share/(?:video|note)/\d+)"
)
UA_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

IP_KEYS = ("ip_location", "ip_attribution", "user_ip_attribution", "publish_ip_location")


# ---------- 输入解析 ----------

def extract_urls(text: str):
    seen, out = set(), []
    for m in URL_RE.findall(text or ""):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def resolve(url: str) -> dict:
    """分享链 → aweme_id / 类型。"""
    m = re.search(r"/(?:video|note)/(\d+)", url)
    if m:
        kind = "note" if "/note/" in url else "video"
        return {"aweme_id": m.group(1), "type": kind, "final_url": url}

    r = requests.get(url, headers={"User-Agent": UA_IOS},
                     allow_redirects=True, timeout=20)
    final = str(r.url)
    m = re.search(r"/(?:video|note)/(\d+)", final)
    if not m:
        m = re.search(r"(\d{15,})", final)
    if not m:
        raise LookupError("短链未跳转到作品页（可能已失效）")
    kind = "note" if "/note/" in final else "video"
    return {"aweme_id": m.group(1), "type": kind, "final_url": final}


# ---------- 抓取 ----------

def capture(aweme_id: str) -> dict:
    proc = subprocess.run(
        ["node", CAPTURE, aweme_id],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        err = (proc.stderr or "").strip()[:300]
        return {"ok": False, "code": "CAPTURE_FAILED", "message": err or "capture 无输出"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "code": "CAPTURE_FAILED",
                "message": "capture 输出非 JSON: " + raw[:200]}


# ---------- 字幕 ----------

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")


def _ms(h, m, s, ms):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def parse_timed_text(text: str):
    """解析 SRT / VTT。"""
    items = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        mm = _TIME_RE.search(block)
        if not mm:
            continue
        lines = [ln for ln in block.split("\n")
                 if ln.strip() and not _TIME_RE.search(ln) and not ln.strip().isdigit()]
        items.append({
            "start_ms": _ms(*mm.group(1, 2, 3, 4)),
            "end_ms": _ms(*mm.group(5, 6, 7, 8)),
            "text": " ".join(ln.strip() for ln in lines).strip(),
        })
    return items


def parse_caption_json(obj):
    """尽量宽容地抽取句子级时间轴。"""
    items, asr_hint = [], False
    seq = None
    if isinstance(obj, dict):
        for k in ("utterances", "sentences", "captions", "body", "data", "results"):
            if isinstance(obj.get(k), list):
                seq = obj[k]
                break
        if seq is None and isinstance(obj.get("text"), str):
            seq = [obj]
        if "confidence" in json.dumps(obj)[:2000]:
            asr_hint = True
    elif isinstance(obj, list):
        seq = obj
    for it in seq or []:
        if not isinstance(it, dict):
            continue
        txt = it.get("text") or it.get("content") or it.get("utterance") or ""
        if not txt:
            continue
        st = it.get("start_time", it.get("start", it.get("start_ms", 0)))
        et = it.get("end_time", it.get("end", it.get("end_ms", 0)))
        st = int(float(st) * (1000 if float(st) < 1e6 else 1))
        et = int(float(et) * (1000 if float(et) < 1e6 else 1))
        if any(k in it for k in ("words", "word_list", "confidence")):
            asr_hint = True
        items.append({"start_ms": st, "end_ms": et, "text": str(txt).strip()})
    return items, asr_hint


def build_subtitles(captions):
    """caption 数组 → 标准字幕轨列表。"""
    tracks = []
    for c in captions or []:
        raw = c.get("text") or ""
        if not raw.strip():
            continue
        fields = c.get("fields") or []
        items, asr_hint, parsed = [], False, "unknown"

        stripped = raw.lstrip()
        if stripped[:1] in "[{":
            try:
                items, asr_hint = parse_caption_json(json.loads(raw))
            except Exception:
                items = []
        if not items:
            items = parse_timed_text(raw)

        basis = ""
        # 1) 显式来源字段
        for k in ("is_author", "author_upload", "is_author_upload"):
            if k in fields:
                v = k  # 仅记录字段名，值未单独回传
                basis = f"字段 {k} 存在"
                parsed = "cc" if c.get("is_author") else parsed
                break
        for k in ("source", "caption_source", "sub_source"):
            if k in fields and c.get(k):
                v = str(c.get(k)).lower()
                if v in ("cc", "author", "user"):
                    parsed, basis = "cc", f"字段 {k}={v}"
                elif v in ("asr", "auto", "machine"):
                    parsed, basis = "asr", f"字段 {k}={v}"
                break
        # 2) ASR 结构特征
        if parsed == "unknown" and asr_hint:
            parsed, basis = "asr", "词级时间戳/confidence 结构"
        if parsed == "unknown":
            basis = "接口未给来源字段，仅句子级时间轴"

        tracks.append({
            "lang": c.get("lang") or "",
            "source": parsed,
            "source_basis": basis,
            "format": c.get("format") or "",
            "count": len(items),
            "items": items,
        })
    return tracks


# ---------- 字段映射 ----------

_PROBE = {}


def _probe(url):
    """带 Range 的裸 GET，判断这条直链在「无 Referer」时能不能打开。

    浏览器点击、地址栏粘贴都不会带 douyin.com 的 Referer，所以只认裸请求的结果。
    部分 douyinvod 节点强制校验 Referer，带上任何非 douyin 的 Referer 也会 403。
    """
    if url in _PROBE:
        return _PROBE[url]
    ok = False
    try:
        r = requests.get(url, headers={"Range": "bytes=0-1023"}, timeout=10, stream=True)
        ok = r.status_code in (200, 206)
        r.close()
    except Exception:
        ok = False
    _PROBE[url] = ok
    return ok


def choose_url(urls, prefer_douyin=True, probe=True):
    """从 url_list 里挑一条用户能直接点开的。

    优先 `www.douyin.com/aweme/v1/play/`：它是 302 跳转，每次落到健康节点，
    不依赖 Referer，比裸 CDN 地址稳。裸地址里有些节点强制要 Referer，点了就 403。
    """
    urls = [u for u in (urls or []) if u]
    if not urls:
        return None
    if prefer_douyin:
        urls = ([u for u in urls if "www.douyin.com/" in u]
                + [u for u in urls if "www.douyin.com/" not in u])
    if not probe:
        return urls[0]
    for u in urls:
        if _probe(u):
            return u
    return urls[0]


def pick_url(node, prefer_douyin=True, probe=True):
    if not isinstance(node, dict):
        return None
    return choose_url(node.get("url_list"), prefer_douyin, probe)


def ts_iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), TZ).isoformat(timespec="seconds")


def find_ip(detail):
    cands = [detail, detail.get("author") or {}]
    for box in cands:
        for k in IP_KEYS:
            v = box.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def build(aweme_id, detail, captions, source_url):
    d = detail
    author = d.get("author") or {}
    video = d.get("video") or {}
    music = d.get("music") or {}
    stats = d.get("statistics") or {}
    images = [u for img in (d.get("images") or []) for u in (img.get("url_list") or [])[:1]]

    is_note = bool(images) and not pick_url(video.get("play_addr"))
    warns = []

    if not find_ip(d):
        warns.append("IP属地：抖音 Web 接口不下发该字段")

    tags = []
    for t in (d.get("text_extra") or []):
        name = t.get("hashtag_name")
        if name and name not in tags:
            tags.append(name)

    subs = build_subtitles(captions)

    expired = video.get("cdn_url_expired")
    quals = extract_qualities(video)

    dead = [q["label"] for q in quals if not _probe(q["url"])]
    if dead:
        warns.append("以下档位直链需 Referer，浏览器直接点可能 403：" + " / ".join(dead))

    return {
        "schema_version": 1,
        "ok": True,
        "aweme_id": d.get("aweme_id") or aweme_id,
        "type": "note" if is_note else "video",
        "url": source_url or f"https://www.douyin.com/video/{aweme_id}",
        "author": {
            "nickname": author.get("nickname"),
            "douyin_id": author.get("unique_id"),
            "sec_uid": author.get("sec_uid"),
            "signature": author.get("signature"),
            "followers": author.get("follower_count"),
            "total_favorited": author.get("total_favorited"),
        },
        "content": {
            "desc": d.get("desc"),
            "hashtags": tags,
            "publish_time": ts_iso(d.get("create_time")),
            "publish_ts": d.get("create_time"),
            "ip_location": find_ip(d),
            "is_ad": bool(d.get("is_ads")),
            "ai_generated": bool(d.get("is_aigc_media")),
        },
        "video": {
            "duration_ms": video.get("duration") or d.get("duration"),
            "width": video.get("width"),
            "height": video.get("height"),
            "play_url": (quals[0]["url"] if quals else None),
            "qualities": quals,
            "download_url": pick_url(video.get("download_addr")),
            "cover_url": pick_url(video.get("cover")),
            "origin_cover_url": pick_url(video.get("origin_cover")),
            "dynamic_cover_url": pick_url(video.get("dynamic_cover")),
            "expires_at": ts_iso(expired),
        },
        "audio": {
            "type": "bgm",
            "title": music.get("title"),
            "author": music.get("author"),
            "duration_ms": int(float(music.get("duration") or 0) * 1000) or None,
            "play_url": pick_url(music.get("play_url")),
            "is_original_sound": bool(music.get("is_original_sound")),
        },
        "images": images or None,
        "subtitles": subs,
        "statistics": {
            "digg": stats.get("digg_count"),
            "comment": stats.get("comment_count"),
            "share": stats.get("share_count"),
            "collect": stats.get("collect_count"),
        },
        "links": {
            "video": (quals[0]["url"] if quals else None),
            "cover": pick_url(video.get("cover")),
            "origin_cover": pick_url(video.get("origin_cover")),
            "dynamic_cover": pick_url(video.get("dynamic_cover")),
            "bgm": pick_url(music.get("play_url")),
            "images": images or None,
        },
        "warnings": warns,
    }


# ---------- 渲染 ----------

def fmt_ms(ms):
    if not ms:
        return "0:00"
    s = int(ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def fmt_size(n):
    if not n:
        return ""
    n = float(n)
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f}M"
    return f"{n / 1024:.0f}K"


_STD_P = (1080, 720, 540, 480, 360, 240)


def _p_label(w, h):
    """按短边归到标准清晰度档（竖屏 1080×1920 → 1080P）。"""
    try:
        short = min(int(w or 0), int(h or 0))
    except (TypeError, ValueError):
        return None
    if not short:
        return None
    return min(_STD_P, key=lambda s: abs(s - short))


def extract_qualities(video):
    """video.bit_rate[] → 去重后的清晰度列表（每档优先 h264，其次码率高）。

    先去重再挑地址：bit_rate 有 11~19 条，逐条探测太慢，只对存活的每档探测一次。
    """
    best = {}
    for b in (video.get("bit_rate") or []):
        pa = b.get("play_addr") or {}
        urls = [u for u in (pa.get("url_list") or []) if u]
        if not urls:
            continue
        w = pa.get("width") or b.get("width")
        h = pa.get("height") or b.get("height")
        p = _p_label(w, h)
        if not p:
            continue
        item = {
            "label": f"{p}P",
            "width": w,
            "height": h,
            "fps": b.get("FPS"),
            "bitrate": b.get("bit_rate"),
            "size": pa.get("data_size"),
            "codec": "h265" if b.get("is_h265") else "h264",
            "urls": urls,
        }
        key = (item["codec"] == "h264", item["bitrate"] or 0)
        prev = best.get(p)
        if prev is None or key > (prev["codec"] == "h264", prev["bitrate"] or 0):
            best[p] = item

    if not best:
        pa = video.get("play_addr") or {}
        urls = [u for u in (pa.get("url_list") or []) if u]
        if urls:
            label = _p_label(pa.get("width"), pa.get("height"))
            best[0] = {
                "label": f"{label}P" if label else "原始",
                "width": pa.get("width"), "height": pa.get("height"),
                "fps": None, "bitrate": None, "size": pa.get("data_size"),
                "codec": "h264", "urls": urls,
            }

    out = []
    for p in sorted(best, reverse=True):
        it = best[p]
        it["url"] = choose_url(it.pop("urls"))
        out.append(it)
    return out


def fmt_cn_time(iso):
    return iso.replace("T", " ")[:16] if iso else "未知"


def clean_desc(desc):
    """正文里的 #话题 已在下面单独展示，这里剥掉避免重复。"""
    if not desc:
        return ""
    s = re.sub(r"#[^\s#]+", "", desc)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def md_link(text, url):
    """Markdown 链接。URL 必须完整——截断签名参数会导致 403。

    用最通用的 `[文本](URL)`，不用 `[文本](<URL>)`：后者是 CommonMark 合法写法，
    但部分渲染器不认，会把整串当普通文本，看起来就是「链接失效」。
    我们的 CDN URL 不含空格和括号，可以安全裸放。
    """
    return f"[{text}]({url})" if url else ""


def _cell(x):
    """表格单元格：去换行、转义竖线。"""
    return str(x).replace("\n", " ").replace("\r", " ").replace("|", "\\|").strip()


def render_md(r):
    if not r.get("ok"):
        return f"❌ 解析失败（{r.get('code')}）：{r.get('message')}"

    a, c, v, au = r["author"], r["content"], r["video"], r["audio"]
    s = r["statistics"]
    rows = []

    who = f"**{a.get('nickname') or '未知作者'}**"
    if a.get("douyin_id"):
        who += f"（抖音号 `{a['douyin_id']}`）"
    rows.append(("👤 作者", who))
    rows.append(("🕒 发布", fmt_cn_time(c.get("publish_time"))))

    if clean_desc(c.get("desc")):
        rows.append(("📝 文案", clean_desc(c["desc"])))
    if c.get("hashtags"):
        rows.append(("🏷️ 标签", " ".join(f"`#{t}`" for t in c["hashtags"])))

    rows.append(("📊 数据", f"👍 {s.get('digg')} · 🗨️ {s.get('comment')} · "
                           f"⭐ {s.get('collect')} · 🔁 {s.get('share')}"))

    spec = f"⏱ {fmt_ms(v.get('duration_ms'))}"
    if v.get("width"):
        spec += f" · {v['width']}×{v['height']}"
    marks = [x for x, on in (("商业推广", c.get("is_ad")),
                             ("AI 生成", c.get("ai_generated"))) if on]
    if marks:
        spec += " · " + " · ".join(marks)
    rows.append(("📐 规格", spec))

    if r["type"] == "note":
        imgs = r.get("images") or []
        rows.append(("🖼️ 图片", f"{len(imgs)} 张　" + " · ".join(
            md_link(f"图{i}", u) for i, u in enumerate(imgs, 1))))
    else:
        quals = v.get("qualities") or []
        links = " · ".join(
            md_link(f"{q['label']}·{fmt_size(q['size'])}".rstrip("·"), q["url"])
            for q in quals
        ) or (md_link("直链", v.get("play_url")) or "无")
        if v.get("expires_at"):
            links += f"　⏳ {fmt_cn_time(v['expires_at'])[11:]} 到期"
        rows.append(("🎬 视频", links))
        covers = [x for x in (md_link("默认", v.get("cover_url")),
                              md_link("原始帧", v.get("origin_cover_url")),
                              md_link("动态", v.get("dynamic_cover_url"))) if x]
        rows.append(("🖼️ 封面", " · ".join(covers) or "无"))

    if au.get("play_url"):
        kind = "原声" if au.get("is_original_sound") else "音乐"
        rows.append(("🎵 BGM", f"{kind} `{au.get('title')}` — {au.get('author')}"
                              f"（{fmt_ms(au.get('duration_ms'))}） "
                              f"{md_link('播放', au['play_url'])}"))
    else:
        rows.append(("🎵 BGM", "无"))

    if r["subtitles"]:
        parts = []
        for t in r["subtitles"]:
            label = {"cc": "CC", "asr": "ASR"}.get(t["source"], "")
            parts.append(f"{t['count']} 条" + (f" · {label}" if label else ""))
        rows.append(("💬 字幕", " / ".join(parts) + "（全文见下）"))
    else:
        rows.append(("💬 字幕", "无 CC/ASR 字幕"))

    L = ["| 项目 | 内容 |", "| --- | --- |"]
    L.extend(f"| {k} | {_cell(val)} |" for k, val in rows)

    if r["subtitles"]:
        L.append("")
        for t in r["subtitles"]:
            label = {"cc": "CC", "asr": "ASR"}.get(t["source"], "")
            L.append("**字幕全文**" + (f"（{label}）" if label else ""))
            L.extend(f"- `{fmt_ms(it['start_ms'])}` {it['text']}" for it in t["items"])

    shown = [w for w in r["warnings"] if "IP属地" not in w]
    if shown:
        L.append("")
        L.append("⚠️ " + " · ".join(shown))
    return "\n".join(L)


# ---------- 主流程 ----------

def run_one(url):
    try:
        info = resolve(url)
    except Exception as e:
        return {"ok": False, "code": "RESOLVE_FAILED", "message": str(e), "url": url}

    cap = capture(info["aweme_id"])
    if not cap.get("ok"):
        cap["url"] = url
        return cap

    try:
        detail = json.loads(cap["detail_text"]).get("aweme_detail")
    except Exception as e:
        return {"ok": False, "code": "NO_DATA", "message": f"detail 解析失败: {e}", "url": url}
    if not detail:
        return {"ok": False, "code": "PRIVATE_OR_DELETED",
                "message": "详情为空（私密/已删除/限流）", "url": url}

    return build(info["aweme_id"], detail, cap.get("captions"), url)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("inputs", nargs="*", help="分享文案 / 短链 / 长链，'-' 表示从 stdin 读")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    ap.add_argument("--raw-out", help="把 detail 原文另存到此路径（排障用）")
    args = ap.parse_args()

    text_parts = []
    if args.inputs:
        for x in args.inputs:
            if x == "-":
                text_parts.append(sys.stdin.read())
            else:
                text_parts.append(x)
    elif not sys.stdin.isatty():
        text_parts.append(sys.stdin.read())
    text = "\n".join(text_parts)

    urls = extract_urls(text)
    if not urls:
        msg = {"ok": False, "code": "BAD_INPUT",
               "message": "没有识别到抖音链接，请发送分享文案或 v.douyin.com 短链"}
        print(json.dumps(msg, ensure_ascii=False, indent=2) if args.json
              else f"❌ {msg['message']}")
        return 2

    results = [run_one(u) for u in urls]

    if args.json:
        payload = results[0] if len(results) == 1 else results
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    blocks = []
    for r in results:
        blocks.append(render_md(r))
    if len(results) > 1:
        okn = sum(1 for r in results if r.get("ok"))
        blocks.insert(0, f"共 {len(results)} 条：成功 {okn} · 失败 {len(results) - okn}\n")
    print("\n\n".join(blocks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
