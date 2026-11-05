#!/usr/bin/env python3
"""Thử các endpoint Shopee Ads KHÁC (GMS / shop-level) để tìm phần chi tiêu campaign còn thiếu.
Bảng campaign hiện chỉ phủ khoảng 44% chi phí (API product-level), các campaign dạng "Tối đa hóa doanh thu"
(vd "Nến Thơm TOP 1") có thể nằm ở API khác. In kết quả thô (cắt gọn) ra log để quyết định."""
import json, datetime as dt
import shopee_ads_api as S

SHOWN = ("Aroma Story - The Art Of Scent",)


def short(o, n=2200):
    s = json.dumps(o, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"... (+{len(s) - n} ký tự)"


def main():
    data = json.load(open(S.TOK_FILE, encoding="utf-8"))
    end = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date()
    start = end - dt.timedelta(days=3)
    dates = {"start_date": S.d2s(start), "end_date": S.d2s(end)}
    for gid, t in data.get("merchants", {}).items():
        S.ensure_fresh(data, gid, t)
        for sid in t.get("shop_id_list", []):
            if sid in S.EXCLUDED_SHOP_IDS:
                continue
            name = S.shop_get("/api/v2/shop/get_shop_info", t["access_token"], sid).get("shop_name")
            if name not in SHOWN:
                continue
            print(f"\n=== {name} ({sid}) ===")
            for path, params in [
                ("/api/v2/ads/get_gms_campaign_performance", dates),
                ("/api/v2/ads/get_gms_item_performance", dates),
                ("/api/v2/ads/get_ads_facade_shop_performance", dates),
                ("/api/v2/ads/get_product_level_campaign_id_list", {"ad_type": "auto"}),
                ("/api/v2/ads/get_shop_toggle_info", {}),
            ]:
                r = S.shop_get(path, t["access_token"], sid, params)
                print(f"\n{path}:", short(r))
    json.dump(data, open(S.TOK_FILE, "w", encoding="utf-8"), indent=2)


if __name__ == "__main__":
    main()
