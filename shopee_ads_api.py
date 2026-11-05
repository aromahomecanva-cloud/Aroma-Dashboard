#!/usr/bin/env python3
"""Kéo dữ liệu Shopee Ads theo NGÀY qua Shopee Open Platform API -> shopee_ads_api_daily.json.

Dùng token ủy quyền trong shopee_tokens.json (Worker /shopee-callback ghi). Tự refresh token khi
sắp hết hạn và ghi token mới lại vào file (workflow commit file này). Có chế độ đối chiếu với các
file CSV thủ công (ngày trùng nhau) để xác định API "expense"/"gmv" khớp cột nào của CSV.

  python shopee_ads_api.py --from 2026-08-01 [--to 2026-11-04] [--compare]
Cần env SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY.
"""
import argparse, hashlib, hmac, json, os, sys, time, datetime as dt
import requests

HOST = os.environ.get("SHOPEE_HOST", "https://partner.shopeemobile.com")
PID = os.environ.get("SHOPEE_PARTNER_ID", "").strip()
KEY = os.environ.get("SHOPEE_PARTNER_KEY", "").strip()
TOK_FILE, OUT_FILE = "shopee_tokens.json", "shopee_ads_api_daily.json"
CHUNK_DAYS = 14


def _sign(path, ts, access_token="", shop_id=""):
    return hmac.new(KEY.encode(), f"{PID}{path}{ts}{access_token}{shop_id}".encode(), hashlib.sha256).hexdigest()


def shop_get(path, tok, shop_id, params=None):
    ts = int(time.time())
    q = {"partner_id": PID, "timestamp": ts, "access_token": tok, "shop_id": shop_id, "sign": _sign(path, ts, tok, shop_id)}
    q.update(params or {})
    r = requests.get(HOST + path, params=q, timeout=90)
    return r.json()


def refresh(refresh_token, ident):
    path = "/api/v2/auth/access_token/get"
    ts = int(time.time())
    r = requests.post(HOST + path, params={"partner_id": PID, "timestamp": ts, "sign": _sign(path, ts)},
                      json={"refresh_token": refresh_token, "partner_id": int(PID), **ident}, timeout=60)
    return r.json()


def ensure_fresh(data, gid, t):
    """Refresh nếu access_token còn < 10 phút. Thử lần lượt merchant_id rồi từng shop_id; ghi token mới vào data."""
    if t["access_expire_at"] > time.time() + 600:
        return False
    tries = [{"merchant_id": int(m)} for m in (t.get("merchant_id_list") or [])] + [{"merchant_id": int(gid)}]
    tries += [{"shop_id": int(s)} for s in (t.get("shop_id_list") or [])]
    for ident in tries:
        res = refresh(t["refresh_token"], ident)
        if res.get("access_token"):
            t.update(access_token=res["access_token"], refresh_token=res.get("refresh_token", t["refresh_token"]),
                     access_expire_at=int(time.time()) + int(res.get("expire_in", 14400)),
                     saved_at=dt.datetime.utcnow().isoformat() + "Z")
            # GHI NGAY: refresh_token cũ có thể bị vô hiệu sau khi đổi -> không được để mất bản mới
            json.dump(data, open(TOK_FILE, "w", encoding="utf-8"), indent=2)
            print(f"[refresh OK] bằng {list(ident)[0]}")
            return True
        print(f"[refresh lỗi] {list(ident)[0]}: {res.get('error')} {res.get('message')}")
    sys.exit("Không refresh được token Shopee -> cần ủy quyền lại qua /shopee-auth")


def d2s(d): return d.strftime("%d-%m-%Y")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--compare", action="store_true")
    a = ap.parse_args()
    if not PID or not KEY:
        sys.exit("Thiếu SHOPEE_PARTNER_ID/SHOPEE_PARTNER_KEY")
    d_from = dt.date.fromisoformat(a.d_from)
    d_to = dt.date.fromisoformat(a.d_to) if a.d_to else dt.date.today()  # gồm cả hôm nay (số tạm thời)
    data = json.load(open(TOK_FILE, encoding="utf-8"))
    rows = []
    for group in ("merchants", "shops"):
        for gid, t in data.get(group, {}).items():
            ensure_fresh(data, gid, t)
            for sid in (t.get("shop_id_list") or ([int(gid)] if group == "shops" else [])):
                info = shop_get("/api/v2/shop/get_shop_info", t["access_token"], sid)
                name = info.get("shop_name") or str(sid)
                cur = d_from
                while cur <= d_to:
                    end = min(cur + dt.timedelta(days=CHUNK_DAYS - 1), d_to)
                    res = shop_get("/api/v2/ads/get_all_cpc_ads_daily_performance", t["access_token"], sid,
                                   {"start_date": d2s(cur), "end_date": d2s(end)})
                    if res.get("error"):
                        print(f"[LỖI] {name} {cur}->{end}: {res.get('error')} {res.get('message')}")
                    for x in res.get("response") or []:
                        dd = dt.datetime.strptime(x["date"], "%d-%m-%Y").date().isoformat()
                        rows.append({"date": dd, "shop_id": sid, "shop_name": name, **{k: x.get(k, 0) for k in (
                            "expense", "impression", "clicks", "direct_order", "broad_order",
                            "direct_gmv", "broad_gmv", "direct_item_sold", "broad_item_sold")}})
                    cur = end + dt.timedelta(days=1)
                print(f"{name}: tổng {sum(1 for r in rows if r['shop_id'] == sid)} ngày")
    json.dump({"updated_at": dt.datetime.utcnow().isoformat() + "Z", "rows": rows},
              open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"Đã ghi {len(rows)} dòng vào {OUT_FILE}")

    if a.compare:
        from business_dashboard_shopee_ads import load_shopee_ads_daily
        csv = load_shopee_ads_daily()
        print("\n=== ĐỐI CHIẾU CSV vs API (các ngày trùng) ===")
        print("shop | ngày | CSV chi phí(trước VAT) | API expense | tỉ lệ API/CSV | CSV doanh số | API direct_gmv | API broad_gmv")
        api = {(r["shop_name"], r["date"]): r for r in rows}
        n = 0
        for _, c in csv.iterrows():
            r = api.get((c["shop_name"], c["date"]))
            if not r or n >= 40:
                continue
            n += 1
            ratio = r["expense"] / c["spend_before_vat"] if c["spend_before_vat"] else float("nan")
            print(f"{c['shop_name']} | {c['date']} | {c['spend_before_vat']:.0f} | {r['expense']:.0f} | {ratio:.4f} | "
                  f"{c['ads_revenue']:.0f} | {r['direct_gmv']:.0f} | {r['broad_gmv']:.0f}")
        print(f"(đã in {n} ngày trùng)")


if __name__ == "__main__":
    main()
