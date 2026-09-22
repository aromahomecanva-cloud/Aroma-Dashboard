// Worker chính cho project Cloudflare "aroma-dashboard" -- làm lớp mật khẩu (Basic Auth) chặn
// TOÀN BỘ request trước khi trả file tĩnh (netlify_site/index.html).
//
// Cloudflare đã gộp Pages vào Workers (2026) -- project mới tạo qua "Workers & Pages" giờ là
// kiểu "Workers with static assets", KHÔNG phải Pages project truyền thống nữa. Vì vậy dùng
// model Worker + assets binding (xem wrangler.toml ở gốc repo: assets.directory trỏ vào
// netlify_site/, assets.run_worker_first=true để Worker này LUÔN chạy trước, không bị Cloudflare
// tự phục vụ file tĩnh trước khi kiểm tra mật khẩu) -- thay cho functions/_middleware.js (kiểu
// Pages Functions cũ, giờ không dùng nữa, có thể xoá file đó sau).
//
// BẮT BUỘC đặt SITE_PASSWORD (và tuỳ chọn SITE_USER) làm Secret/Variable của Worker trong
// Cloudflare dashboard (chọn worker > Settings > Variables and Secrets) -- KHÔNG hardcode,
// KHÔNG commit vào git.

const encoder = new TextEncoder();

// So sánh chuỗi kiểu "timing-safe" (chống dò mật khẩu qua đo thời gian phản hồi).
// https://developers.cloudflare.com/workers/runtime-apis/web-crypto/#timingsafeequal
function timingSafeEqual(a, b) {
  const aBytes = encoder.encode(a);
  const bBytes = encoder.encode(b);
  if (aBytes.byteLength !== bBytes.byteLength) {
    return !crypto.subtle.timingSafeEqual(aBytes, aBytes);
  }
  return crypto.subtle.timingSafeEqual(aBytes, bBytes);
}

function unauthorized() {
  return new Response("Unauthorized", {
    status: 401,
    headers: {
      // Trình duyệt tự hiện popup xin username/password khi thấy header này.
      "WWW-Authenticate": 'Basic realm="Aroma Dashboard", charset="UTF-8"',
    },
  });
}

export default {
  async fetch(request, env) {
    const expectedPassword = env.SITE_PASSWORD;
    const expectedUser = env.SITE_USER || "aroma";

    // Fail-closed: nếu QUÊN cấu hình SITE_PASSWORD, chặn hết TẤT CẢ request (503) thay vì lỡ
    // để lộ toàn bộ dashboard ra công khai không mật khẩu.
    if (!expectedPassword) {
      return new Response(
        "Site chưa được cấu hình mật khẩu (thiếu biến môi trường SITE_PASSWORD). " +
          "Vào Cloudflare dashboard > Compute (Workers) > chọn worker \"aroma-dashboard\" > " +
          "Settings > Variables and Secrets để thêm.",
        { status: 503 }
      );
    }

    const authorization = request.headers.get("Authorization");
    if (!authorization) return unauthorized();

    const [scheme, encoded] = authorization.split(" ");
    if (scheme !== "Basic" || !encoded) return unauthorized();

    let user = "";
    let pass = "";
    try {
      const decoded = atob(encoded);
      const idx = decoded.indexOf(":");
      user = decoded.substring(0, idx);
      pass = decoded.substring(idx + 1);
    } catch (e) {
      return unauthorized();
    }

    if (!timingSafeEqual(user, expectedUser) || !timingSafeEqual(pass, expectedPassword)) {
      return unauthorized();
    }

    // Đăng nhập đúng -> phục vụ file tĩnh thật (index.html...) qua binding ASSETS.
    return env.ASSETS.fetch(request);
  },
};
