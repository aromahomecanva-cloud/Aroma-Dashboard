// Ủy quyền Shopee Open Platform (v2) cho app "Shopee Ads Manager" -- chạy sau Basic Auth của Worker.
//
//   /shopee-auth     -> chuyển tới trang Shopee để CHỦ SHOP đăng nhập + đồng ý ủy quyền
//   /shopee-callback -> Shopee chuyển về đây kèm ?code=...&shop_id=... ; Worker đổi code lấy
//                       access_token/refresh_token rồi LƯU vào file shopee_tokens.json trong repo
//                       (qua GitHub API, dùng GITHUB_TOKEN sẵn có) để pipeline GitHub Actions dùng.
//                       Token KHÔNG được hiện ra màn hình.
//
// Biến cần có (Secret, Production): SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY (bản LIVE), GITHUB_TOKEN.
// Tuỳ chọn: GITHUB_REPO (mặc định aromahomecanva-cloud/Aroma-Dashboard), SHOPEE_HOST.
//
// LƯU Ý: chữ ký/endpoint viết theo tài liệu Shopee Open Platform v2 theo hiểu biết của tác giả,
// CHƯA chạy thử với API thật -- nếu Shopee trả lỗi, trang callback in nguyên văn lỗi để chẩn đoán.

const enc = new TextEncoder();

async function hmacHex(key, msg) {
  const k = await crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", k, enc.encode(msg));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function html(body, status = 200) {
  return new Response(`<!doctype html><meta charset="utf-8"><body style="font-family:system-ui;max-width:640px;margin:40px auto;line-height:1.5">${body}</body>`, {
    status, headers: { "content-type": "text/html; charset=utf-8" },
  });
}

async function saveTokens(env, shopId, tok) {
  const repo = env.GITHUB_REPO || "aromahomecanva-cloud/Aroma-Dashboard";
  const url = `https://api.github.com/repos/${repo}/contents/shopee_tokens.json`;
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
    "User-Agent": "aroma-dashboard-worker", "X-GitHub-Api-Version": "2022-11-28",
  };
  for (let attempt = 0; attempt < 3; attempt++) {
    let sha, data = { shops: {} };
    const cur = await fetch(url + "?ref=main", { headers });
    if (cur.ok) {
      const j = await cur.json();
      sha = j.sha;
      try { data = JSON.parse(atob(j.content.replace(/\n/g, ""))); } catch (e) { /* file hỏng -> tạo lại */ }
    } else if (cur.status !== 404) {
      throw new Error(`GitHub đọc file lỗi ${cur.status}: ${await cur.text()}`);
    }
    data.shops = data.shops || {};
    data.shops[String(shopId)] = {
      refresh_token: tok.refresh_token,
      access_token: tok.access_token,
      access_expire_at: Math.floor(Date.now() / 1000) + (tok.expire_in || 14400),
      saved_at: new Date().toISOString(),
    };
    const put = await fetch(url, {
      method: "PUT", headers,
      body: JSON.stringify({
        message: `Shopee: lưu token cho shop ${shopId}`, branch: "main", sha,
        content: btoa(JSON.stringify(data, null, 2)),
      }),
    });
    if (put.ok) return;
    if (put.status !== 409 && put.status !== 422) throw new Error(`GitHub ghi file lỗi ${put.status}: ${await put.text()}`);
  }
  throw new Error("GitHub ghi file bị xung đột 3 lần, thử lại sau ít phút.");
}

export async function shopeeRoute(request, env, path) {
  const pid = env.SHOPEE_PARTNER_ID, key = env.SHOPEE_PARTNER_KEY;
  if (!pid || !key) return html("Thiếu SHOPEE_PARTNER_ID / SHOPEE_PARTNER_KEY trong Variables and Secrets của Worker.", 503);
  const host = env.SHOPEE_HOST || "https://partner.shopeemobile.com";
  const origin = new URL(request.url).origin;
  const ts = () => Math.floor(Date.now() / 1000);

  if (path === "/shopee-auth") {
    const apiPath = "/api/v2/shop/auth_partner", t = ts();
    const sign = await hmacHex(key, `${pid}${apiPath}${t}`);
    const redirect = encodeURIComponent(`${origin}/shopee-callback`);
    return Response.redirect(`${host}${apiPath}?partner_id=${pid}&timestamp=${t}&sign=${sign}&redirect=${redirect}`, 302);
  }

  // /shopee-callback
  const q = new URL(request.url).searchParams;
  const code = q.get("code"), shopId = q.get("shop_id");
  if (!code || !shopId) {
    return html(`Không thấy <code>code</code>/<code>shop_id</code> trên đường dẫn (nếu bạn ủy quyền bằng tài khoản chủ/merchant thì Shopee gửi <code>main_account_id</code> thay vì <code>shop_id</code>). Đường dẫn nhận được: <code>${new URL(request.url).search.replace(/</g, "&lt;")}</code>`, 400);
  }
  const apiPath = "/api/v2/auth/token/get", t = ts();
  const sign = await hmacHex(key, `${pid}${apiPath}${t}`);
  const r = await fetch(`${host}${apiPath}?partner_id=${pid}&timestamp=${t}&sign=${sign}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ code, shop_id: Number(shopId), partner_id: Number(pid) }),
  });
  const tok = await r.json().catch(() => ({}));
  if (!r.ok || tok.error || !tok.refresh_token) {
    return html(`<b>Shopee từ chối đổi token.</b><br>HTTP ${r.status}<br><code>${JSON.stringify({ error: tok.error, message: tok.message, request_id: tok.request_id }).replace(/</g, "&lt;")}</code><br>Mã <code>code</code> chỉ dùng được 1 lần và hết hạn sau vài phút -- bấm lại <a href="/shopee-auth">/shopee-auth</a> để ủy quyền lại.`, 502);
  }
  try {
    await saveTokens(env, shopId, tok);
  } catch (e) {
    return html(`Đổi token OK nhưng LƯU vào repo lỗi: <code>${String(e.message).replace(/</g, "&lt;")}</code>`, 500);
  }
  return html(`<h3>Đã ủy quyền xong cho shop <code>${shopId}</code></h3>Token đã được lưu vào repo (<code>shopee_tokens.json</code>), không hiển thị ở đây. Muốn ủy quyền thêm shop khác: đăng xuất tài khoản Shopee của shop hiện tại rồi mở lại <a href="/shopee-auth">/shopee-auth</a>.`);
}
