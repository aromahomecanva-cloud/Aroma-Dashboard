#!/usr/bin/env python3
"""Thử lấy tên campaign NHÓM SẢN PHẨM / "Tối đa hóa doanh thu" (vd "Nến Thơm TOP 1") qua các endpoint GMS.
Lần chạy 06/11/2026: get_gms_* trả ads_rate_limit_total_api dù chờ thử lại 7 phút -> bản này gọi GMS NGAY ĐẦU
PHIÊN (trước mọi lời gọi khác, không dùng retry dài) để loại trừ trường hợp hết hạn mức do các lần gọi trước,
rồi thử thêm các giá trị ad_type của danh sách campaign. In kết quả thô (cắt gọn) ra log."""
import json, time, datetime as dt
import shopee_ads_api as S

SHOP_ID = 727941567  # The Art Of Scent


def short(o, n=2500):
    s = json.dumps(o, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"... (+{len(s) - n} ký tự)"


def once(path, tok, params):
    r = S._shop_get_once(path, tok, SHOP_ID, params)  # không retry: xem lỗi nguyên bản
    print(f"\n{path} {params}:", short(r))
    time.sleep(3)
    return r


def main():
    data = json.load(open(S.TOK_FILE, encoding="utf-8"))
    t = data["merchants"][next(iter(data["merchants"]))]
    S.ensure_fresh(data, next(iter(data["merchants"])), t)
    gid = next(iter(data["merchants"]))
    print(f"token lưu lúc {t.get('saved_at')}, còn {(t['access_expire_at'] - time.time()) / 60:.0f} phút theo bản ghi")
    # kiểm tra token còn dùng được không; Shopee báo invalid_acceess_token (đã gặp 06/11/2026 dù bản ghi còn hạn,
    # có thể do token vừa bị lần chạy khác làm mới) -> ép refresh rồi dùng token mới
    chk = S._shop_get_once("/api/v2/shop/get_shop_info", t["access_token"], SHOP_ID)
    print("kiểm tra token:", chk.get("error") or "OK", chk.get("message") or "")
    if chk.get("error"):
        S.ensure_fresh(data, gid, t, force=True)
    tok = t["access_token"]
    end = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date()
    dates = {"start_date": S.d2s(end - dt.timedelta(days=3)), "end_date": S.d2s(end)}
    once("/api/v2/ads/get_gms_campaign_performance", tok, dates)
    once("/api/v2/ads/get_gms_campaign_performance", tok, {**dates, "offset": 0, "limit": 20})
    once("/api/v2/ads/get_gms_item_performance", tok, dates)
    for ad_type in ("gms", "group", "product_group", "gmv_max", "shop"):
        once("/api/v2/ads/get_product_level_campaign_id_list", tok, {"ad_type": ad_type})
    json.dump(data, open(S.TOK_FILE, "w", encoding="utf-8"), indent=2)


if __name__ == "__main__":
    main()
