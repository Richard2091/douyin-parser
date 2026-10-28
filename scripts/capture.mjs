// capture.mjs — 用本机 Chrome 打开抖音作品页，拦截页面自身发出的 detail 接口响应。
// 零依赖：只用 Chrome + Node 内置 WebSocket / child_process。
// 用法: node capture.mjs <aweme_id>
// 输出: 单行 JSON { ok, detail_text, captions[], url, elapsed_ms, code, message }

import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = process.env.CHROME_PATH ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const AID = (process.argv[2] || '').replace(/\D/g, '');
const PORT = 9400 + Math.floor(Math.random() * 500);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const out = (o) => { process.stdout.write(JSON.stringify(o)); };

if (!AID) { out({ ok: false, code: 'BAD_INPUT', message: '缺少 aweme_id' }); process.exit(2); }

const profile = mkdtempSync(join(tmpdir(), 'dycap-'));
let chrome;
try {
  chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--mute-audio', '--window-size=1280,900',
    'about:blank',
  ], { stdio: 'ignore', windowsHide: true });
} catch (e) {
  out({ ok: false, code: 'NO_BROWSER', message: 'Chrome 启动失败: ' + e.message });
  process.exit(3);
}

let wsUrl = null;
for (let i = 0; i < 50; i++) {
  await sleep(200);
  try {
    const j = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
    if (j.webSocketDebuggerUrl) { wsUrl = j.webSocketDebuggerUrl; break; }
  } catch { }
}
if (!wsUrl) {
  chrome.kill();
  out({ ok: false, code: 'NO_BROWSER', message: 'Chrome CDP 未就绪（端口被占或被策略阻止）' });
  process.exit(3);
}

const created = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`,
  { method: 'PUT' })).json();
const ws = new WebSocket(created.webSocketDebuggerUrl);
const pending = new Map();
let msgId = 0;
const send = (method, params = {}) => new Promise((res, rej) => {
  const id = ++msgId;
  pending.set(id, { res, rej });
  ws.send(JSON.stringify({ id, method, params }));
});

const hits = [];
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id);
    m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    return;
  }
  if (m.method === 'Network.responseReceived') {
    const { url, status } = m.params.response;
    if (url.includes('aweme/v1/web/aweme/detail') && status === 200) {
      hits.push({ requestId: m.params.requestId, url });
    }
  }
});

await new Promise((r) => ws.addEventListener('open', r));
await send('Network.enable', { maxResourceBufferSize: 200 * 1024 * 1024 });
await send('Page.enable');
await send('Network.setUserAgentOverride', {
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
             '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  acceptLanguage: 'zh-CN,zh;q=0.9',
});

const t0 = Date.now();
const pageUrl = `https://www.douyin.com/video/${AID}`;
await send('Page.navigate', { url: pageUrl });

for (let i = 0; i < 80 && !hits.length; i++) await sleep(200);

function finish(obj, code = 0) {
  try { ws.close(); } catch { }
  try { chrome.kill(); } catch { }
  out(obj);
  setTimeout(() => process.exit(code), 200);
}

if (!hits.length) {
  // 没抓到接口，看页面是不是被风控/需要登录
  let dom = '';
  try {
    const r = await send('Runtime.evaluate',
      { expression: 'document.body.innerText || ""', returnByValue: true });
    dom = (r.result?.value || '').slice(0, 400);
  } catch { }
  const blocked = /验证|captcha|登录|异常|稍后再试/.test(dom);
  finish({
    ok: false,
    code: blocked ? 'RISK_BLOCKED' : 'NO_DATA',
    message: blocked
      ? '页面触发风控/登录墙，未拿到 detail 响应'
      : '页面已打开但未发出 detail 请求（作品可能已删除或不可见）',
    dom_head: dom.replace(/\s+/g, ' ').slice(0, 160),
    url: pageUrl,
  }, 4);
}

let detailText = null;
for (const h of hits) {
  try {
    const b = await send('Network.getResponseBody', { requestId: h.requestId });
    const txt = b.base64Encoded ? Buffer.from(b.body, 'base64').toString('utf8') : b.body;
    if (txt.includes('aweme_detail') && txt.length > (detailText?.length || 0)) detailText = txt;
  } catch { }
}

if (!detailText) {
  finish({ ok: false, code: 'NO_DATA', message: 'detail 响应为空或已过期', url: pageUrl }, 4);
}

// 有字幕轨则顺带在页面上下文里取回内容（带 cookie/referer，避免 CDN 403）
let captions = [];
try {
  const raw = JSON.parse(detailText);
  const d = raw.aweme_detail || {};
  const infos = d?.video?.cla_info?.caption_infos;
  if (Array.isArray(infos) && infos.length) {
    const urls = infos.map((c) => ({
      lang: c.lang || c.language_code || '',
      format: c.caption_format || c.format || '',
      sub_id: c.sub_id || c.id || '',
      url: (c.url || (c.url_list || [])[0] || ''),
      fields: Object.keys(c),
    })).filter((c) => c.url);
    if (urls.length) {
      const expr = `(async () => {
        const list = ${JSON.stringify(urls)};
        const res = [];
        for (const c of list) {
          try {
            const r = await fetch(c.url, { credentials: 'include' });
            res.push({ ...c, status: r.status, text: await r.text() });
          } catch (e) { res.push({ ...c, status: 0, text: '', error: String(e) }); }
        }
        return res;
      })()`;
      const r = await send('Runtime.evaluate',
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result?.value) captions = r.result.value;
    }
  }
} catch { }

finish({
  ok: true,
  detail_text: detailText,
  captions,
  url: pageUrl,
  elapsed_ms: Date.now() - t0,
}, 0);
