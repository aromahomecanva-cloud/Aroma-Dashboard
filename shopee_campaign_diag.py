#!/usr/bin/env python3
"""Chẩn đoán API Shopee Ads cấp CAMPAIGN/SẢN PHẨM (để dựng bảng chi tiết ads như Seller Centre).
Với mỗi shop Shopee chính: liệt kê campaign, lấy thông tin cài đặt (tên/trạng thái/loại) và hiệu suất theo ngày,
in kết quả (cắt gọn, không token) ra log. Chạy bằng workflow "Shopee campaign diag" (bấm tay)."""
import json, datetime as dt, sys
import shopee_ads_api as S

SHOWN = ("Aroma Story - Góc Hương Thơm", "Aroma Story - The Art Of Scent")


def short(o, n=1800):
    s = json.dumps(o, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"... (+{len(s) - n} ký tự)"


def main():
    data = json.load(open(S.TOK_FILE, encoding="utf-8"))
    end = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date()
    start = end - dt.timedelta(days=6)
    for gid, t in data.get("merchants", {}).items():
        for sid in t.get("shop_id_list", []):
            if sid in S.EXCLUDED_SHOP_IDS:
                continue
            tok = S.shop_token(data, gid, t, sid)
            if not tok:
                continue
            name = S.shop_get("/api/v2/shop/get_shop_info", tok, sid).get("shop_name")
            if name not in SHOWN:
                continue
            print(f"\n=== {name} ({sid}) ===")
            r = S.shop_get("/api/v2/ads/get_product_level_campaign_id_list", tok, sid, {"ad_type": "all"})
            print("campaign_id_list:", short(r, 1500))
            lst = (r.get("response") or {}).get("campaign_list") or []
            ids = [str(c["campaign_id"]) for c in lst][:5]
            print(f"tổng {len(lst)} campaign; thử {len(ids)} đầu")
            if not ids:
                continue
            r = S.shop_get("/api/v2/ads/get_product_level_campaign_setting_info", tok, sid,
                           {"info_type_list": "1,2,3,4", "campaign_id_list": ",".join(ids)})
            print("setting_info:", short(r))
            r = S.shop_get("/api/v2/ads/get_product_campaign_daily_performance", tok, sid,
                           {"start_date": S.d2s(start), "end_date": S.d2s(end), "campaign_id_list": ",".join(ids[:3])})
            print("daily_performance:", short(r))
    json.dump(data, open(S.TOK_FILE, "w", encoding="utf-8"), indent=2)


if __name__ == "__main__":
    main()
