#!/usr/bin/env python3
"""Chẩn đoán kết nối Shopee Open Platform (chạy bằng workflow "Shopee API diag", bấm tay).

Đọc shopee_tokens.json (do Worker /shopee-callback ghi), với MỖI shop được cấp quyền:
  1. get_shop_info          -> biết shop_id nào là shop nào
  2. get_all_cpc_ads_daily_performance (7 ngày gần nhất) -> thử lấy chi tiêu/doanh số ads
In kết quả (đã cắt gọn, KHÔNG in token) ra log để quyết định cách viết bước kéo dữ liệu chính thức.
Cần env SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY (GitHub Secrets).
"""
import hashlib, hmac, json, os, sys, time, datetime as dt
import requests

HOST = os.environ.get("SHOPEE_HOST", "https://partner.shopeemobile.com")
PID = os.environ.get("SHOPEE_PARTNER_ID", "").strip()
KEY = os.environ.get("SHOPEE_PARTNER_KEY", "").strip()
TOK_FILE = "shopee_tokens.json"


def sign(path, ts, access_token="", shop_id=""):
    base = f"{PID}{path}{ts}{access_token}{shop_id}"
    return hmac.new(KEY.encode(), base.encode(), hashlib.sha256).hexdigest()


def shop_call(path, access_token, shop_id, params=None):
    ts = int(time.time())
    q = {"partner_id": PID, "timestamp": ts, "access_token": access_token, "shop_id": shop_id,
         "sign": sign(path, ts, access_token, shop_id)}
    q.update(params or {})
    r = requests.get(HOST + path, params=q, timeout=60)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:300]}


def refresh(refresh_token, **ident):
    path = "/api/v2/auth/access_token/get"
    ts = int(time.time())
    body = {"refresh_token": refresh_token, "partner_id": int(PID), **ident}
    r = requests.post(HOST + path, params={"partner_id": PID, "timestamp": ts, "sign": sign(path, ts)}, json=body, timeout=60)
    return r.status_code, r.json()


def short(o, n=700):
    s = json.dumps(o, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"... (+{len(s) - n} ký tự)"


def main():
    if not PID or not KEY:
        sys.exit("Thiếu SHOPEE_PARTNER_ID/SHOPEE_PARTNER_KEY")
    data = json.load(open(TOK_FILE, encoding="utf-8"))
    changed = False
    today = dt.date.today()
    start, end = (today - dt.timedelta(days=7)).strftime("%d-%m-%Y"), (today - dt.timedelta(days=1)).strftime("%d-%m-%Y")
    for group in ("merchants", "shops"):
        for gid, t in data.get(group, {}).items():
            shop_ids = t.get("shop_id_list") or ([int(gid)] if group == "shops" else [])
            print(f"\n=== {group} {gid}: {len(shop_ids)} shop ===")
            if t["access_expire_at"] < time.time() + 300:
                print("access_token hết hạn -> refresh")
                ident = {"merchant_id": int(gid)} if group == "merchants" else {"shop_id": int(gid)}
                code, res = refresh(t["refresh_token"], **ident)
                print("refresh:", code, short({k: v for k, v in res.items() if "token" not in k}))
                if res.get("access_token"):
                    t.update(access_token=res["access_token"], refresh_token=res.get("refresh_token", t["refresh_token"]),
                             access_expire_at=int(time.time()) + res.get("expire_in", 14400))
                    changed = True
            for sid in shop_ids:
                print(f"\n--- shop_id {sid} ---")
                c, r = shop_call("/api/v2/shop/get_shop_info", t["access_token"], sid)
                print("get_shop_info:", c, short({k: r.get(k) for k in ("shop_name", "region", "status", "is_cb", "error", "message") if k in r} or r))
                c, r = shop_call("/api/v2/ads/get_all_cpc_ads_daily_performance", t["access_token"], sid,
                                 {"start_date": start, "end_date": end})
                print(f"ads daily {start}->{end}:", c, short(r))
                c, r = shop_call("/api/v2/ads/get_total_balance", t["access_token"], sid)
                print("ads balance:", c, short(r, 300))
    if changed:
        json.dump(data, open(TOK_FILE, "w", encoding="utf-8"), indent=2)
        print("\nĐã cập nhật token mới vào", TOK_FILE)


if __name__ == "__main__":
    main()
