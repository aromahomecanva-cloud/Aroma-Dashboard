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
CAMP_META_FILE, CAMP_DAILY_FILE = "shopee_campaigns.json", "shopee_campaign_api_daily.json"
BATCH = 50  # số campaign mỗi lần gọi setting_info / daily_performance
CHUNK_DAYS = 14
# Shop đã được ủy quyền nhưng KHÔNG kéo dữ liệu (Huy yêu cầu 05/11/2026; khi nào cần thì xóa khỏi danh sách này)
EXCLUDED_SHOP_IDS = {1289495643}  # Tiệm Trà Nhà Quê - Since 2022


def _sign(path, ts, access_token="", shop_id=""):
    return hmac.new(KEY.encode(), f"{PID}{path}{ts}{access_token}{shop_id}".encode(), hashlib.sha256).hexdigest()


def shop_get(path, tok, shop_id, params=None):
    """GET có ký; tự chờ + thử lại khi dính error_rate_limit (Shopee giới hạn khá chặt)."""
    for attempt in range(6):
        res = _shop_get_once(path, tok, shop_id, params)
        # có nhiều mã rate limit: error_rate_limit, ads_rate_limit_total_api... -> thử lại với mọi mã chứa "rate_limit"
        if "rate_limit" not in str(res.get("error") or ""):
            time.sleep(1.2)
            return res
        wait = 10 * (attempt + 1)
        print(f"  [rate limit] chờ {wait}s rồi thử lại ({attempt + 1}/6)")
        time.sleep(wait)
    return res


def _shop_get_once(path, tok, shop_id, params=None):
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


def ensure_fresh(data, gid, t, force=False):
    """Refresh nếu access_token còn < 10 phút (hoặc force=True khi Shopee báo token không hợp lệ). Thử lần lượt
    merchant_id rồi từng shop_id; ghi token mới vào data."""
    if not force and t["access_expire_at"] > time.time() + 600:
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


def shop_token(data, gid, t, sid):
    """access_token DÙNG ĐƯỢC CHO ĐÚNG shop `sid`.

    Phát hiện 06/11/2026: sau lần refresh đầu tiên bằng shop_id, token trả về CHỈ dùng được cho shop đó (shop khác
    báo invalid_acceess_token), còn refresh bằng merchant_id báo merchant_no_linked -> phải refresh TỪNG shop bằng
    shop_id của nó. Token từng shop lưu ở t["shop_tokens"][shop_id] = {access_token, refresh_token, access_expire_at};
    thử lần lượt các refresh_token đang có (của chính shop, của shop khác, của bản ghi gốc) cho tới khi shop nhận được
    token. Ghi file NGAY sau mỗi lần refresh thành công (refresh_token cũ có thể bị vô hiệu)."""
    store = t.setdefault("shop_tokens", {})
    st = store.get(str(sid))
    if st and st["access_expire_at"] > time.time() + 600:
        return st["access_token"]
    cands = []
    for rt in [st and st.get("refresh_token")] + [v.get("refresh_token") for v in store.values()] + [t.get("refresh_token")]:
        if rt and rt not in cands:
            cands.append(rt)
    for rt in cands:
        res = refresh(rt, {"shop_id": int(sid)})
        if res.get("access_token"):
            store[str(sid)] = {"access_token": res["access_token"], "refresh_token": res.get("refresh_token", rt),
                               "access_expire_at": int(time.time()) + int(res.get("expire_in", 14400)),
                               "saved_at": dt.datetime.utcnow().isoformat() + "Z"}
            t["refresh_token"] = res.get("refresh_token", rt)
            json.dump(data, open(TOK_FILE, "w", encoding="utf-8"), indent=2)
            print(f"[refresh OK] shop {sid}")
            return res["access_token"]
        print(f"[refresh lỗi] shop {sid}: {res.get('error')} {res.get('message')}")
    print(f"[LỖI] không refresh được token cho shop {sid} -> cần ủy quyền lại qua /shopee-auth")
    return None


def d2s(d): return d.strftime("%d-%m-%Y")


def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def fetch_campaigns(data, d_from, d_to):
    """Danh sách campaign (tên/trạng thái/loại/ngân sách) + hiệu suất THEO NGÀY từng campaign của các shop.

    - shopee_campaigns.json: {campaign_id: {shop_id, shop_name, name, status, bidding, placement, budget,
      start, end, roas_target}} -- làm mới toàn bộ mỗi lần (trạng thái đổi liên tục).
    - shopee_campaign_api_daily.json: {rows:[{date, shop_id, campaign_id, impression, clicks, expense, broad_gmv,
      broad_order, broad_item_sold}]} -- chỉ lưu ngày có phát sinh; gộp theo (campaign_id, date), ghi đè khoảng
      [d_from, d_to] của shop vừa xử lý. Chỉ hỏi hiệu suất campaign chưa kết thúc trước d_from (end=0 hoặc end>=d_from).
    """
    meta_all = _load(CAMP_META_FILE, {})
    rows_all = _load(CAMP_DAILY_FILE, {}).get("rows", [])
    since_ts = int(dt.datetime.combine(d_from, dt.time.min).timestamp()) - 86400
    for gid, t in data.get("merchants", {}).items():
        for sid in t.get("shop_id_list", []):
            if sid in EXCLUDED_SHOP_IDS:
                continue
            tok = shop_token(data, gid, t, sid)
            if not tok:
                continue
            name = shop_get("/api/v2/shop/get_shop_info", tok, sid).get("shop_name") or str(sid)
            res = shop_get("/api/v2/ads/get_product_level_campaign_id_list", tok, sid, {"ad_type": "all"})
            ids = [str(c["campaign_id"]) for c in (res.get("response") or {}).get("campaign_list", [])]
            print(f"[campaign] {name}: {len(ids)} campaign")
            if not ids:
                print("  ", res.get("error"), res.get("message"))
                continue
            meta = {}
            for i in range(0, len(ids), BATCH):
                r = shop_get("/api/v2/ads/get_product_level_campaign_setting_info", tok, sid,
                             {"info_type_list": "1,3", "campaign_id_list": ",".join(ids[i:i + BATCH])})
                if r.get("error"):
                    print(f"  [LỖI setting_info] {r.get('error')} {r.get('message')}")
                for c in (r.get("response") or {}).get("campaign_list", []):
                    ci = c.get("common_info") or {}
                    dur = ci.get("campaign_duration") or {}
                    ab = c.get("auto_bidding_info") or {}
                    meta[str(c.get("campaign_id"))] = {
                        "shop_id": sid, "shop_name": name, "name": ci.get("ad_name"), "ad_type": ci.get("ad_type"),
                        "status": ci.get("campaign_status"), "bidding": ci.get("bidding_method"),
                        "placement": ci.get("campaign_placement"), "budget": ci.get("campaign_budget"),
                        "start": dur.get("start_time"), "end": dur.get("end_time"), "roas_target": ab.get("roas_target"),
                    }
            meta_all = {k: v for k, v in meta_all.items() if v.get("shop_id") != sid}
            meta_all.update(meta)
            cand = [c for c, m in meta.items() if not m.get("end") or m["end"] >= since_ts]
            print(f"  setting_info: {len(meta)} campaign; hỏi hiệu suất {len(cand)} campaign chưa kết thúc từ {d_from}")
            rows_all = [r for r in rows_all if not (r["shop_id"] == sid and d_from.isoformat() <= r["date"] <= d_to.isoformat())]
            got = 0
            for i in range(0, len(cand), BATCH):
                batch = cand[i:i + BATCH]
                cur = d_from
                while cur <= d_to:
                    end = min(cur + dt.timedelta(days=CHUNK_DAYS - 1), d_to)
                    r = shop_get("/api/v2/ads/get_product_campaign_daily_performance", tok, sid,
                                 {"start_date": d2s(cur), "end_date": d2s(end), "campaign_id_list": ",".join(batch)})
                    if r.get("error"):
                        print(f"  [LỖI daily_performance] {cur}->{end}: {r.get('error')} {r.get('message')}")
                    lst = (r.get("response") or {}).get("campaign_list", [])
                    for k, e in enumerate(lst):
                        cid = str(e.get("campaign_id") or (batch[k] if len(lst) == len(batch) else ""))
                        if not cid:
                            continue
                        for m in e.get("metrics_list", []):
                            if not (m.get("impression") or m.get("expense")):
                                continue
                            rows_all.append({
                                "date": dt.datetime.strptime(m["date"], "%d-%m-%Y").date().isoformat(), "shop_id": sid,
                                "campaign_id": cid, **{f: m.get(f, 0) for f in (
                                    "impression", "clicks", "expense", "broad_gmv", "broad_order", "broad_item_sold")}})
                            got += 1
                    cur = end + dt.timedelta(days=1)
            print(f"  hiệu suất: {got} dòng campaign-ngày có phát sinh")
    rows_all.sort(key=lambda r: (r["shop_id"], r["campaign_id"], r["date"]))
    json.dump(meta_all, open(CAMP_META_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    json.dump({"updated_at": dt.datetime.utcnow().isoformat() + "Z", "rows": rows_all},
              open(CAMP_DAILY_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"Đã ghi {len(meta_all)} campaign vào {CAMP_META_FILE}, {len(rows_all)} dòng vào {CAMP_DAILY_FILE}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--campaigns", action="store_true", help="kéo thêm danh sách + hiệu suất từng campaign")
    a = ap.parse_args()
    if not PID or not KEY:
        sys.exit("Thiếu SHOPEE_PARTNER_ID/SHOPEE_PARTNER_KEY")
    d_from = dt.date.fromisoformat(a.d_from)
    # hôm nay theo GIỜ VN (runner GitHub chạy giờ UTC, tránh lệch ngày), gồm cả hôm nay (số tạm thời)
    vn_today = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date()
    d_to = dt.date.fromisoformat(a.d_to) if a.d_to else vn_today
    data = json.load(open(TOK_FILE, encoding="utf-8"))
    rows = []
    for group in ("merchants", "shops"):
        for gid, t in data.get(group, {}).items():
            for sid in (t.get("shop_id_list") or ([int(gid)] if group == "shops" else [])):
                if sid in EXCLUDED_SHOP_IDS:
                    continue
                if group == "merchants":
                    tok = shop_token(data, gid, t, sid)
                else:  # "shops": ủy quyền từng shop (token riêng của shop)
                    ensure_fresh(data, gid, t)
                    tok = t["access_token"]
                if not tok:
                    continue
                info = shop_get("/api/v2/shop/get_shop_info", tok, sid)
                name = info.get("shop_name") or str(sid)
                cur = d_from
                while cur <= d_to:
                    end = min(cur + dt.timedelta(days=CHUNK_DAYS - 1), d_to)
                    res = shop_get("/api/v2/ads/get_all_cpc_ads_daily_performance", tok, sid,
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
    # GỘP với file cũ theo (shop_id, date) -- chạy incremental (vài ngày gần nhất) không làm mất lịch sử
    try:
        old = json.load(open(OUT_FILE, encoding="utf-8")).get("rows", [])
    except Exception:
        old = []
    merged = {(r["shop_id"], r["date"]): r for r in old}
    merged.update({(r["shop_id"], r["date"]): r for r in rows})
    out_rows = sorted((r for r in merged.values() if r["shop_id"] not in EXCLUDED_SHOP_IDS),
                      key=lambda r: (r["shop_id"], r["date"]))
    json.dump({"updated_at": dt.datetime.utcnow().isoformat() + "Z", "rows": out_rows},
              open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"Đã ghi {len(out_rows)} dòng (mới lấy {len(rows)}) vào {OUT_FILE}")

    if a.campaigns:
        fetch_campaigns(data, d_from, d_to)

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
