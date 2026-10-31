// Bot Telegram đọc phản hồi của Huy về cảnh báo Facebook Ads (do ads_alerts.py gửi).
//
// Luồng: Telegram -> POST /tg-webhook (Worker này) -> đọc ads_alert_state.json + ads_alert_ack.json
// trên GitHub -> hiểu tin nhắn (lệnh nhanh, hoặc nhờ Claude nếu có ANTHROPIC_API_KEY) -> nếu Huy
// nói "giữ" thì ghi thêm vào ads_alert_ack.json (bot thôi nhắc) -> trả lời trên Telegram.
//
// Secrets cần đặt trong Cloudflare (worker aroma-dashboard > Settings > Variables and Secrets):
//   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (giống secrets trên GitHub)
//   TG_WEBHOOK_SECRET   chuỗi ngẫu nhiên tự đặt, chống người lạ gọi webhook
//   GITHUB_TOKEN        fine-grained token, chỉ repo Aroma-Dashboard, quyền Contents: Read and write
//   ANTHROPIC_API_KEY   (tuỳ chọn) dùng Claude để hiểu câu tự do; không có thì dùng Workers AI miễn phí (binding AI)
// Biến tuỳ chọn: GITHUB_REPO (mặc định aromahomecanva-cloud/Aroma-Dashboard), ANTHROPIC_MODEL.
// Sau khi đặt xong: đăng nhập dashboard rồi mở <domain>/tg-setup một lần để đăng ký webhook.

const DEFAULT_REPO = "aromahomecanva-cloud/Aroma-Dashboard";

const b64decode = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), (c) => c.charCodeAt(0)));
const b64encode = (s) => {
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
};

// Bỏ dấu cách/xuống dòng thừa khi dán secret vào Cloudflare
function clean(env) {
  const out = Object.create(env);
  for (const k of ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TG_WEBHOOK_SECRET", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "GITHUB_REPO", "ANTHROPIC_MODEL"]) {
    if (typeof env[k] === "string") out[k] = env[k].trim();
  }
  return out;
}

function todayVN() {
  return new Date(Date.now() + 7 * 3600e3).toISOString().slice(0, 10);
}

function money(x) {
  if (x >= 1e6) return (x / 1e6).toFixed(2).replace(/\.?0+$/, "").replace(".", ",") + "tr";
  return Math.round(x / 1000) + "k";
}

// ---------- GitHub ----------
async function gh(env, path, opts = {}) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  const r = await fetch(`https://api.github.com/repos/${repo}/contents/${path}`, {
    ...opts,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "aroma-ads-bot",
      ...(opts.headers || {}),
    },
  });
  if (!r.ok) throw new Error(`GitHub ${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

async function readJson(env, path) {
  const f = await gh(env, `${path}?ref=main`);
  return { data: JSON.parse(b64decode(f.content)), sha: f.sha };
}

async function addAcks(env, newAcks) {
  // Thử lại 1 lần nếu file vừa bị đổi (sha lệch)
  for (let attempt = 0; attempt < 2; attempt++) {
    const { data, sha } = await readJson(env, "ads_alert_ack.json");
    data.acks = data.acks || [];
    for (const a of newAcks) {
      data.acks = data.acks.filter((x) => String(x.id) !== String(a.id));
      data.acks.push(a);
    }
    try {
      await gh(env, "ads_alert_ack.json", {
        method: "PUT",
        body: JSON.stringify({
          message: `Bot: giữ ${newAcks.map((a) => a.name).join(", ")}`,
          content: b64encode(JSON.stringify(data, null, 2) + "\n"),
          sha,
          branch: "main",
        }),
      });
      return;
    } catch (e) {
      if (attempt === 1) throw e;
    }
  }
}

// ---------- Telegram ----------
async function reply(env, chatId, text, replyTo) {
  for (let i = 0; i < text.length; i += 3800) {
    const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text: text.slice(i, i + 3800),
        reply_to_message_id: i === 0 ? replyTo : undefined,
        disable_web_page_preview: true,
      }),
    });
    if (!r.ok) console.error("Telegram sendMessage", r.status, await r.text());
  }
}

// ---------- Hiểu tin nhắn ----------
function parseUntil(text) {
  const m = text.match(/(?:tới|đến|toi|den|until)\s*(?:ngày\s*)?(\d{1,2})[\/\-.](\d{1,2})(?:[\/\-.](\d{2,4}))?/i);
  if (!m) return "";
  const today = todayVN();
  let y = m[3] ? (m[3].length === 2 ? 2000 + +m[3] : +m[3]) : +today.slice(0, 4);
  let d = `${y}-${String(m[2]).padStart(2, "0")}-${String(m[1]).padStart(2, "0")}`;
  if (!m[3] && d < today) d = `${y + 1}${d.slice(4)}`;
  return d;
}

// Một item cảnh báo "giữ" nghĩa là gì: item cần tắt ad set -> ghi nhận ad set;
// item chỉ có ad xấu -> ghi nhận từng ad xấu bên trong.
function acksForItem(item, reason, until, adId) {
  const base = { reason: reason || "(không ghi lý do)", until: until || "", added: todayVN(), by: "telegram" };
  if (adId) {
    const ad = item.ads.find((a) => String(a.id) === String(adId));
    return ad ? [{ id: ad.id, name: `Ad ${ad.name}`, ...base }] : [];
  }
  if (item.level === "off") return [{ id: item.id, name: `Ad set ${item.name} (${item.campaign})`, ...base }];
  return item.ads.map((a) => ({ id: a.id, name: `Ad ${a.name}`, ...base }));
}

function quickParse(text) {
  const t = text.trim();
  let m = t.match(/^(?:giữ|giu|keep)\s+(#?\d+(?:\s*(?:,|&|và)?\s*#?\d+)*)(.*)$/i);
  if (m) {
    const codes = [...m[1].matchAll(/\d+/g)].map((x) => +x[0]);
    const rest = m[2].trim();
    const reason = rest.replace(/^(?:vì|vi|do|:|-|bởi vì)\s*/i, "").replace(/\s*(?:tới|đến|toi|den|until)\s.*$/i, "").trim();
    return { kind: "keep", codes, reason, until: parseUntil(rest) };
  }
  m = t.match(/^(?:đã\s*tắt|da\s*tat|tắt rồi|tat roi|off)\s+(#?\d+(?:\s*(?:,|&|và)?\s*#?\d+)*)$/i);
  if (m) return { kind: "off", codes: [...m[1].matchAll(/\d+/g)].map((x) => +x[0]) };
  if (/^\/?(?:list|ds|danh sách|danh sach|xem lại|xem lai)$/i.test(t)) return { kind: "list" };
  if (/^\/?(?:help|start|hướng dẫn|huong dan)$/i.test(t)) return { kind: "help" };
  if (/^\/?(?:hôm nay|hom nay|today|tổng|tong|tổng quan|tong quan)$/i.test(t)) return { kind: "today" };
  return null;
}

const HELP_CMDS =
  "• hôm nay → tổng quan chi tiêu, đơn, ROAS hôm nay + 7 ngày\n" +
  "• list → xem lại cảnh báo đang mở\n" +
  "• giữ 3 vì đang test creative mới tới 5/11 → thôi nhắc #3 (tới ngày đó, bỏ trống = mãi mãi)\n" +
  "• đã tắt 1 2 → ghi nhận, lần chạy sau tự kiểm tra trên Meta";
const hasLLM = (env) => Boolean(env.ANTHROPIC_API_KEY || env.AI);
const help = (env) =>
  "Mình đọc được các lệnh:\n" + HELP_CMDS +
  (hasLLM(env)
    ? "\n• hỏi tự do, VD: \"#1 mấy ngày nay chạy sao?\""
    : "\n\nHỏi tự do (VD \"ads hôm nay sao\") chưa bật.");

const ddmm = (d) => d.slice(8, 10) + "/" + d.slice(5, 7);
const roasTxt = (v) => (v[0] > 0 && v[2] > 0 ? ` · ROAS ${(v[2] / v[0]).toFixed(2).replace(".", ",")}` : "");
const line = (v) => `${money(v[0])} · ${Math.round(v[1])} đơn${roasTxt(v)}`;

function todayText(state) {
  const sm = state.summary;
  if (!sm?.account_daily) return "Chưa có số liệu tổng quan, đợi lần cập nhật dashboard tới (mỗi 2 tiếng).";
  const days = Object.keys(sm.account_daily).sort();
  const last = days[days.length - 1];
  const sum = (vs) => vs.reduce((a, v) => a.map((x, i) => x + v[i]), [0, 0, 0, 0]);
  const tot7 = sum(Object.values(sm.account_daily));
  const upd = state.updated_at ? ` (cập nhật ${state.updated_at.slice(11, 16)} UTC)` : "";
  let out = `📊 Facebook Ads ${ddmm(last)}${upd}\nHôm nay: ${line(sm.account_daily[last])}\n7 ngày: ${line(tot7)}\n`;
  out += "\nTheo ngày:\n" + days.slice().reverse().map((d) => `${ddmm(d)}: ${line(sm.account_daily[d])}`).join("\n");
  const camps = (sm.campaigns || [])
    .map((c) => ({ name: c.name, today: c.daily[last] || [0, 0, 0, 0], w: sum(Object.values(c.daily)) }))
    .sort((a, b) => b.today[0] - a.today[0] || b.w[0] - a.w[0]);
  if (camps.length)
    out += "\n\nTheo campaign (hôm nay | 7 ngày):\n" +
      camps.map((c) => `• ${c.name}\n  ${line(c.today)} | ${line(c.w)}`).join("\n");
  out += "\n\nLưu ý: số hôm nay chưa trọn ngày, đơn/doanh thu có thể về trễ.";
  return out;
}

function listText(state) {
  if (!state.items?.length) return `Không còn cảnh báo nào (dữ liệu đến ${state.date}).`;
  return (
    `Cảnh báo đang mở (dữ liệu đến ${state.date}):\n` +
    state.items
      .map(
        (it) =>
          `#${it.code} ${it.level === "off" ? "🔴" : "🟡"} ${it.name} – ${money(it.spend)}, ${it.orders} đơn` +
          (it.ads.length ? ` (${it.ads.length} ad xấu)` : "")
      )
      .join("\n")
  );
}

async function askLLM(env, text, state, acks, quoted) {
  const system =
    "Bạn là trợ lý Facebook Ads của Huy (shop nến/tinh dầu Aroma). Bot đã gửi cảnh báo ad set/ad nên tắt, " +
    "đánh số #code. Huy trả lời trên Telegram. Hiểu ý Huy và trả về DUY NHẤT một JSON:\n" +
    '{"reply": "câu trả lời tiếng Việt ngắn gọn, thẳng, không markdown", ' +
    '"acks": [{"code": số, "ad_id": "chỉ khi Huy muốn giữ đúng 1 ad cụ thể, không thì bỏ", "reason": "lý do Huy nêu", "until": "YYYY-MM-DD hoặc rỗng"}]}\n' +
    "Chỉ thêm vào acks khi Huy NÓI RÕ muốn giữ/không tắt/thôi nhắc. Không có lý do thì vẫn ghi, reason để rỗng. " +
    "Nếu Huy nói đã tắt thì acks rỗng, reply xác nhận lần chạy sau bot tự kiểm tra trên Meta. " +
    "Câu hỏi về số liệu thì trả lời dựa trên dữ liệu bên dưới: alerts.summary là tổng quan toàn tài khoản và từng campaign 7 ngày " +
    "(mỗi ngày = [chi tiêu, đơn, doanh thu, kết quả theo mục tiêu campaign]; ngày mới nhất chưa trọn ngày); items[].daily_7d = [ngày, chi tiêu, đơn, doanh thu] của ad set bị cảnh báo; " +
    "không có dữ liệu thì nói thẳng là không có, không bịa. Nếu Huy giữ một ad set ROAS rất thấp mà không có lý do rõ, " +
    "vẫn ghi nhận nhưng nhắc ngắn một câu về số tiền đang mất. Tiền là VND.";
  const ctx = {
    today: todayVN(),
    alerts: state,
    current_acks: acks,
    message_being_replied_to: quoted || null,
  };
  const user = `DỮ LIỆU:\n${JSON.stringify(ctx)}\n\nTIN NHẮN CỦA HUY:\n${text}`;
  let out;
  if (env.ANTHROPIC_API_KEY) {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: env.ANTHROPIC_MODEL || "claude-haiku-5-5",
        max_tokens: 1200,
        system,
        messages: [{ role: "user", content: user }],
      }),
    });
    if (!r.ok) throw new Error(`Claude API ${r.status}: ${await r.text()}`);
    out = (await r.json()).content.map((c) => c.text || "").join("");
  } else {
    // Miễn phí: Cloudflare Workers AI (binding "AI" trong wrangler.toml), 10.000 neurons/ngày
    const r = await env.AI.run(env.AI_MODEL || "@cf/meta/llama-3.3-70b-instruct-fp8-fast", {
      messages: [{ role: "system", content: system + " Chỉ trả về JSON, không viết gì ngoài JSON." }, { role: "user", content: user }],
      max_tokens: 1200,
    });
    const resp = r?.response;
    if (resp && typeof resp === "object") return resp; // model đôi khi trả sẵn JSON đã parse
    out = String(resp ?? "");
  }
  const j = out.match(/\{[\s\S]*\}/);
  if (!j) return { reply: out.trim(), acks: [] };
  try {
    return JSON.parse(j[0]);
  } catch {
    return { reply: out.trim(), acks: [] };
  }
}

async function handleUpdate(update, env) {
  const msg = update.message || update.edited_message;
  if (!msg?.text) return;
  const chatId = msg.chat.id;
  if (String(chatId) !== String(env.TELEGRAM_CHAT_ID)) {
    // Không im lặng nữa: báo chat ID thật để sửa secret TELEGRAM_CHAT_ID (không đọc/ghi dữ liệu gì)
    console.log("chat id mismatch", chatId);
    return reply(env, chatId, `Bot chưa được cấu hình cho chat này. Chat ID của chat này là ${chatId}. Nếu đây là chat nhận cảnh báo, đặt secret TELEGRAM_CHAT_ID = ${chatId} trong Cloudflare.`, msg.message_id);
  }
  const text = msg.text.replace(/@\w+bot\b/gi, "").trim();

  try {
    const [{ data: state }, { data: ackFile }] = await Promise.all([
      readJson(env, "ads_alert_state.json"),
      readJson(env, "ads_alert_ack.json"),
    ]);
    const items = state.items || [];
    const byCode = Object.fromEntries(items.map((it) => [it.code, it]));
    const q = quickParse(text);

    if (q?.kind === "help") return reply(env, chatId, help(env), msg.message_id);
    if (q?.kind === "today") return reply(env, chatId, todayText(state), msg.message_id);
    if (q?.kind === "list") return reply(env, chatId, listText(state), msg.message_id);
    if (q?.kind === "off") {
      const found = q.codes.filter((c) => byCode[c]);
      return reply(
        env, chatId,
        found.length
          ? `Ok, đã ghi nhận tắt ${found.map((c) => `#${c} ${byCode[c].name}`).join(", ")}. Lần chạy tới bot kiểm tra trạng thái trên Meta, đã tắt thật thì tự biến mất.`
          : `Không thấy mã ${q.codes.join(", ")} trong cảnh báo gần nhất.\n\n${listText(state)}`,
        msg.message_id
      );
    }

    let plan; // {reply, acks:[{code, ad_id, reason, until}]}
    if (q?.kind === "keep") {
      plan = { acks: q.codes.map((c) => ({ code: c, reason: q.reason, until: q.until })) };
    } else if (hasLLM(env)) {
      plan = await askLLM(env, text, state, ackFile.acks || [], msg.reply_to_message?.text);
    } else {
      return reply(env, chatId, "Câu hỏi tự do chưa bật (thiếu Workers AI hoặc ANTHROPIC_API_KEY). Hiện mình chỉ hiểu các lệnh:\n" + HELP_CMDS, msg.message_id);
    }

    const newAcks = [];
    const missing = [];
    for (const a of plan.acks || []) {
      const it = byCode[a.code];
      if (!it) { missing.push(a.code); continue; }
      newAcks.push(...acksForItem(it, a.reason, a.until, a.ad_id));
    }
    if (newAcks.length) await addAcks(env, newAcks);

    let out = plan.reply || "";
    if (newAcks.length) {
      const untilTxt = newAcks[0].until ? ` tới ${newAcks[0].until.split("-").reverse().slice(0, 2).join("/")}` : "";
      out += (out ? "\n\n" : "") +
        `✅ Đã ghi, bot thôi nhắc${untilTxt}:\n` + newAcks.map((a) => `• ${a.name} – ${a.reason}`).join("\n");
    }
    if (missing.length) out += `\n\nKhông thấy mã ${missing.map((c) => "#" + c).join(", ")} trong cảnh báo ngày ${state.date}.`;
    await reply(env, chatId, out.trim() || "Mình chưa hiểu ý bạn.\n\n" + help(env), msg.message_id);
  } catch (e) {
    console.error("handleUpdate", e);
    await reply(env, chatId, `Bot gặp lỗi, chưa ghi được gì: ${String(e.message || e).slice(0, 300)}`, msg.message_id);
  }
}

// POST /tg-webhook: Telegram gọi vào. Trả 200 ngay, xử lý phía sau để Telegram không gửi lại.
export async function telegramWebhook(request, rawEnv, ctx) {
  const env = clean(rawEnv);
  const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
  if (!env.TG_WEBHOOK_SECRET || secret !== env.TG_WEBHOOK_SECRET) return new Response("forbidden", { status: 403 });
  const update = await request.json().catch(() => null);
  if (update) ctx.waitUntil(handleUpdate(update, env));
  return new Response("ok");
}

// GET /tg-setup (sau khi đăng nhập dashboard): đăng ký webhook với Telegram.
export async function telegramSetup(request, rawEnv) {
  const env = clean(rawEnv);
  const missing = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TG_WEBHOOK_SECRET", "GITHUB_TOKEN"].filter((k) => !env[k]);
  if (missing.length) return new Response(`Thiếu secret: ${missing.join(", ")}`, { status: 500 });
  const url = new URL("/tg-webhook", request.url).toString();
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/setWebhook`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, secret_token: env.TG_WEBHOOK_SECRET, allowed_updates: ["message", "edited_message"] }),
  });
  const j = await r.json();
  const ai = env.ANTHROPIC_API_KEY ? "Claude API" : env.AI ? "Cloudflare Workers AI (miễn phí)" : "chưa có (chỉ hiểu lệnh)";
  return new Response(
    `${j.ok ? "✅ Đã đăng ký webhook" : "❌ Lỗi"}: ${url}\n${JSON.stringify(j)}\nHỏi tự do: ${ai}`,
    { headers: { "Content-Type": "text/plain; charset=utf-8" } }
  );
}
