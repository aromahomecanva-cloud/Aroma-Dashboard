// Basic Auth cho toàn bộ site Cloudflare Pages -- thay thế Netlify Edge Function (netlify/
// edge-functions/auth.js) khi chuyển từ Netlify sang Cloudflare Pages (22/09/2026).
//
// Lý do chuyển: gói free Netlify chỉ có 300 credit/tháng, mỗi lần deploy production tốn 15
// credit -> chỉ ~20 lần deploy/tháng, KHÔNG đủ cho lịch chạy 8 lần/ngày của workflow (~240
// lần/tháng). Cloudflare Pages free cho 500 lần deploy/tháng, thoải mái hơn nhiều.
//
// Cloudflare Pages tự động nhận file này vì nó nằm trong thư mục /functions Ở GỐC REPO (không
// phải trong thư mục static output netlify_site/) -- xem docs: "Make sure that the /functions
// directory is at the root of your Pages project (and not in the static root)". _middleware.js
// áp dụng cho MỌI route trong site (không cần khai báo path riêng).
//
// BẮT BUỘC đặt 2 biến môi trường sau trong Cloudflare dashboard (Pages project > Settings >
// Environment variables) -- KHÔNG hardcode, KHÔNG commit vào git:
//   - SITE_PASSWORD (bắt buộc): mật khẩu dùng chung cho cả team xem dashboard.
//   - SITE_USER (tuỳ chọn): tên đăng nhập, mặc định "aroma" nếu bỏ qua.

const encoder = new TextEncoder();

// So sánh chuỗi kiểu "timing-safe" (chống dò mật khẩu qua đo thời gian phản hồi) -- dùng
// crypto.subtle.timingSafeEqual có sẵn trong Workers runtime (Cloudflare Pages Functions chạy
// trên nền Workers). Xem https://developers.cloudflare.com/workers/runtime-apis/web-crypto/#timingsafeequal
function timingSafeEqual(a, b) {
  const aBytes = encoder.encode(a);
  const bBytes = encoder.encode(b);
  // Không được return sớm khi độ dài khác nhau -- lộ luôn độ dài của secret qua thời gian xử lý.
  // So sánh với chính nó rồi phủ định thay vì so sánh 2 mảng khác độ dài trực tiếp.
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

export async function onRequest(context) {
  const { request, env, next } = context;

  const expectedPassword = env.SITE_PASSWORD;
  const expectedUser = env.SITE_USER || "aroma";

  // Fail-closed: nếu QUÊN cấu hình SITE_PASSWORD trong Cloudflare dashboard, chặn hết TẤT CẢ
  // request (503) thay vì lỡ để lộ toàn bộ dashboard ra công khai không mật khẩu.
  if (!expectedPassword) {
    return new Response(
      "Site chưa được cấu hình mật khẩu (thiếu biến môi trường SITE_PASSWORD). " +
        "Vào Cloudflare dashboard > Workers & Pages > chọn project > Settings > " +
        "Environment variables để thêm.",
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

  // Đăng nhập đúng -> cho request đi tiếp tới file tĩnh thật (index.html...).
  return next();
}
