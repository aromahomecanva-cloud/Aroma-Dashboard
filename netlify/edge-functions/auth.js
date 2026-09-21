// Basic-Auth gate for the internal Aroma dashboard.
// Password is stored as Netlify environment variables (Site settings -> Environment
// variables), never in the client-side code, so it isn't visible via "View source".
// Set SITE_USER and SITE_PASSWORD once in the Netlify dashboard for this site.

export default async (request, context) => {
  const expectedUser = Netlify.env.get("SITE_USER") || "aroma";
  const expectedPass = Netlify.env.get("SITE_PASSWORD");

  // Fail CLOSED if no password has been configured yet, so the dashboard is never
  // accidentally left wide open because someone forgot to set the env var.
  if (!expectedPass) {
    return new Response(
      "Dashboard chưa được cấu hình mật khẩu (thiếu biến môi trường SITE_PASSWORD trên Netlify). " +
        "Vào Netlify -> Site settings -> Environment variables để thêm.",
      { status: 503 }
    );
  }

  const authHeader = request.headers.get("authorization");
  if (authHeader && authHeader.startsWith("Basic ")) {
    try {
      const decoded = atob(authHeader.slice(6));
      const sep = decoded.indexOf(":");
      const user = decoded.slice(0, sep);
      const pass = decoded.slice(sep + 1);
      if (user === expectedUser && pass === expectedPass) {
        return context.next();
      }
    } catch (_e) {
      // fall through to 401 below
    }
  }

  return new Response("Cần đăng nhập để xem dashboard nội bộ.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Aroma Dashboard", charset="UTF-8"',
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
};

export const config = { path: "/*" };
