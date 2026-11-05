#!/usr/bin/env python3
"""Build the standalone static dashboard site (netlify_site/index.html) from data.json.

This reuses the exact same head.html/tail.html injection approach as the Cowork
artifact build (see the dashboard-refresh skill), so the two stay visually identical.
Runs as part of the GitHub Actions pipeline right after data.json is regenerated,
and the output gets deployed to Netlify (see .github/workflows/update_dashboard.yml).
"""
import argparse
import datetime as dt
import json
import os
import sys


def build_reviewer_page(data, out_path, template="dashboard_template/reviewer.html"):
    """Trang CHỈ-SHOPEE-ADS cho tài khoản xét duyệt app Shopee Open Platform (REVIEW_USER trong
    worker/index.js chỉ được phục vụ file này). Dữ liệu được LỌC Ở SERVER khi build -- chỉ nhúng
    chi tiêu ads, doanh số ads, doanh thu shop và số đơn của Shopee; KHÔNG có COGS, gross margin,
    phí, Facebook Ads, chi phí vận hành... nên view-source cũng không lộ."""
    try:
        with open(template, encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        print(f"WARNING: không thấy {template}, bỏ qua reviewer.html", file=sys.stderr)
        return
    ads = {}
    for r in data.get("shopee_ads_daily", []):
        k = (r["date"], r["shop_page"])
        ads.setdefault(k, {"date": r["date"], "shop": r["shop_page"], "spend": 0.0, "ads_rev": 0.0})["spend"] += r.get("spend") or 0
    for r in data.get("shopee_ads_revenue_daily", []):
        k = (r["date"], r["shop_name"])
        ads.setdefault(k, {"date": r["date"], "shop": r["shop_name"], "spend": 0.0, "ads_rev": 0.0})["ads_rev"] += r.get("ads_revenue") or 0
    shop_sales = [
        {"date": r["date"], "shop": r["shop_page"], "sales": r.get("gross_revenue") or 0, "orders": r.get("orders") or 0}
        for r in data.get("daily", []) if r.get("channel") == "shopee" and r.get("shop_page")
    ]
    campaigns = [
        {"date": r["date"], "shop": r["shop_name"], "name": r["campaign_name"],
         "spend": r.get("spend") or 0, "ads_rev": r.get("ads_revenue") or 0}
        for r in data.get("shopee_ads_campaign_daily", [])
    ]
    payload = {"ads": list(ads.values()), "shop_sales": shop_sales, "campaigns": campaigns}
    html = html.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out_path} ({len(html) / 1024:,.0f} KiB) — trang Shopee Ads cho người xét duyệt.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--head", default="dashboard_template/head.html")
    ap.add_argument("--tail", default="dashboard_template/tail.html")
    ap.add_argument("--out", default="netlify_site/index.html")
    ap.add_argument("--overhead", default="monthly_overhead.json")
    ap.add_argument("--shopee-api", default="shopee_ads_api_daily.json")
    ap.add_argument("--shopee-campaigns", default="shopee_campaigns.json")
    ap.add_argument("--shopee-campaign-daily", default="shopee_campaign_api_daily.json")
    args = ap.parse_args()

    try:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"FAILED to parse {args.data}: {e}", file=sys.stderr)
        sys.exit(1)

    # Chi phí vận hành hàng tháng (nhân viên + mặt bằng) -- file RIÊNG, KHÔNG nằm trong data.json
    # vì đây là số Huy tự nhập tay (từ bảng lương/hợp đồng thuê), không lấy từ Sapo/Meta API nào.
    # Không bắt buộc phải có file này (dashboard vẫn chạy bình thường, chỉ là thiếu 2 card "Chi
    # phí vận hành"/"Lợi nhuận ròng ước tính" ở tab Tổng quan -- xem computeOverheadForRange() ở
    # dashboard_template/tail.html).
    try:
        with open(args.overhead, encoding="utf-8") as f:
            monthly_overhead = json.load(f)
    except FileNotFoundError:
        monthly_overhead = {}
    except Exception as e:
        print(f"WARNING: failed to parse {args.overhead}: {e} -- bỏ qua, dashboard vẫn build bình thường.", file=sys.stderr)
        monthly_overhead = {}

    # Shopee Ads theo ngày từ Open API (lượt xem, click, đơn, GMV, chi phí...) -- file riêng do
    # shopee_ads_api.py ghi, KHÔNG nằm trong data.json. Thiếu file thì phần "Shopee Ads chi tiết" để trống.
    try:
        with open(args.shopee_api, encoding="utf-8") as f:
            shopee_api_daily = json.load(f).get("rows", [])
    except Exception:
        shopee_api_daily = []

    # Danh sách + hiệu suất theo ngày từng campaign Shopee Ads (bảng "Chi tiết campaign"); thiếu file -> bảng trống.
    try:
        with open(args.shopee_campaigns, encoding="utf-8") as f:
            camp_meta = json.load(f)
        with open(args.shopee_campaign_daily, encoding="utf-8") as f:
            camp_rows = json.load(f).get("rows", [])
    except Exception:
        camp_meta, camp_rows = {}, []
    shopee_campaign_api = {"meta": camp_meta, "rows": camp_rows}

    daily = data.get("daily", [])
    ads_daily = data.get("ads_daily", [])
    product_breakdown = data.get("product_breakdown", [])
    ads_detail = data.get("ads_detail", {})
    campaigns = ads_detail.get("campaigns", [])
    adsets = ads_detail.get("adsets", [])
    campaigns_daily = data.get("ads_campaigns_daily", [])
    adsets_daily = data.get("ads_adsets_daily", [])
    ads_ads_daily = data.get("ads_ads_daily", [])
    ad_post_links = data.get("ad_post_links", {})
    shopee_ads_revenue_daily = data.get("shopee_ads_revenue_daily", [])
    shopee_ads_campaign_daily = data.get("shopee_ads_campaign_daily", [])

    updated_at_raw = data.get("updated_at", "")
    try:
        updated_dt = dt.datetime.fromisoformat(updated_at_raw)
        updated_vn = updated_dt + dt.timedelta(hours=7)
        updated_str = updated_vn.strftime("%Y-%m-%d %H:%M") + " (giờ VN)"
    except Exception:
        updated_str = updated_at_raw or "(không rõ)"

    with open(args.head, encoding="utf-8") as f:
        head = f.read()
    with open(args.tail, encoding="utf-8") as f:
        tail = f.read()

    if "__UPDATED_AT__" not in head:
        print("WARNING: __UPDATED_AT__ placeholder not found in head template.", file=sys.stderr)
    head = head.replace("__UPDATED_AT__", updated_str)

    def j(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    middle = (
        f"    const dailyData = {j(daily)};\n"
        f"    const adsDailyData = {j(ads_daily)};\n"
        f"    const productBreakdown = {j(product_breakdown)};\n"
        f"    const campaignsData = {j(campaigns)};\n"
        f"    const adsetsData = {j(adsets)};\n"
        f"    const campaignsDailyData = {j(campaigns_daily)};\n"
        f"    const adsetsDailyData = {j(adsets_daily)};\n"
        f"    const adLevelDailyData = {j(ads_ads_daily)};\n"
        f"    const adPostLinks = {j(ad_post_links)};\n"
        f"    const monthlyOverhead = {j(monthly_overhead)};\n"
        f"    const shopeeAdsRevenueDaily = {j(shopee_ads_revenue_daily)};\n"
        f"    const shopeeAdsCampaignDaily = {j(shopee_ads_campaign_daily)};\n"
        f"    const shopeeAdsApiDaily = {j(shopee_api_daily)};\n"
        f"    const shopeeCampaignApi = {j(shopee_campaign_api)};\n\n"
    )

    full = head + "\n" + middle + tail

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(full)

    size_kb = len(full.encode("utf-8")) / 1024
    print(
        f"Wrote {args.out} ({size_kb:,.0f} KiB). updated_at={updated_str}. "
        f"daily rows={len(daily)}, campaigns={len(campaigns)}, adsets={len(adsets)}."
    )

    build_reviewer_page(data, os.path.join(os.path.dirname(args.out) or ".", "reviewer.html"))

    missing = data.get("shopee_ads_missing_dates")
    if missing:
        for shop, gaps in missing.items():
            if gaps:
                print(f"NOTE: {shop} is missing Shopee Ads data for: {', '.join(gaps)}")


if __name__ == "__main__":
    main()
