"""
CHẠY: python business_dashboard_main.py

In tiến trình từng bước ra màn hình, và xuất file dashboard.html cạnh script này.
Nếu chưa điền API key thật (.env), tool tự chạy DEMO_MODE với dữ liệu mẫu.
"""

import sys

from business_dashboard_config import Config
from business_dashboard_sapo import get_orders, get_variant_sku_map
from business_dashboard_costs import load_cost_map
from business_dashboard_meta import get_ads_spend
from business_dashboard_settlement import load_settlement_fees
from business_dashboard_aggregate import build_summary
from business_dashboard_render import render_html


def step(n, text):
    print(f"\n[Bước {n}] {text}")


def main():
    print("=" * 60)
    print(" BUSINESS DASHBOARD — Shopee / TikTok Shop / Ads")
    print("=" * 60)
    if Config.DEMO_MODE:
        print(" Chế độ: DEMO (chưa điền API key thật — xem file env.example.txt)")
    else:
        print(" Chế độ: LIVE (đang lấy dữ liệu thật từ Sapo + Meta)")

    step(1, "Lấy đơn hàng từ Sapo (Shopee + TikTok Shop)...")
    orders = get_orders(days=30)
    print(f"   -> Lấy được {len(orders)} đơn hàng trong 30 ngày gần nhất.")

    step(2, "Lấy mapping variant->SKU từ Sapo + đọc file product_costs.csv...")
    variant_sku_map = get_variant_sku_map()
    cost_map = load_cost_map()
    print(f"   -> {len(variant_sku_map)} variant, {len(cost_map)} SKU có giá vốn trong file.")

    step(3, "Lấy chi phí quảng cáo từ Meta Marketing API...")
    ads_data = get_ads_spend(days=30)
    print(f"   -> Tổng chi phí Ads: {ads_data['total_spend']:,.0f}đ ({len(ads_data['campaigns'])} campaign)")

    step(4, "Đọc file đối soát Shopee / TikTok Shop (nếu có)...")
    settlement_df = load_settlement_fees()
    print(f"   -> Đọc được {len(settlement_df)} dòng phí đối soát.")

    step(5, "Tổng hợp: doanh thu gộp / thực nhận / COGS / gross margin theo kênh...")
    summary = build_summary(orders, variant_sku_map, cost_map, ads_data, settlement_df)
    for _, r in summary.iterrows():
        label = f"{r['channel']} / {r['shop_page']}" if r['shop_page'] else r['channel']
        print(f"   • {label}: DT gộp {r['gross_revenue']:,.0f}đ | "
              f"DT thực nhận {r['net_revenue']:,.0f}đ | "
              f"Gross margin {r['gross_margin_amount']:,.0f}đ ({r['gross_margin_pct']}%) | "
              f"Ads {r['ads_spend']:,.0f}đ | LN sau ads {r['net_profit_after_ads']:,.0f}đ")

    step(6, "Xuất dashboard.html...")
    html = render_html(summary)
    Config.OUTPUT_DASHBOARD_HTML.write_text(html, encoding="utf-8")
    print(f"   -> Đã lưu: {Config.OUTPUT_DASHBOARD_HTML}")

    print(f"\nTổng ads spend (Meta, chưa gán kênh): {ads_data.get('total_spend', 0):,.0f}đ")
    print("\nXong! Mở file dashboard.html bằng trình duyệt để xem.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[LỖI] {e}", file=sys.stderr)
        sys.exit(1)
