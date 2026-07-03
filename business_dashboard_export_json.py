"""
Giống business_dashboard_main.py, nhưng xuất thêm data.json (dùng cho GitHub Actions
đẩy dữ liệu về, để Claude đọc và cập nhật dashboard artifact).

Lấy TOÀN BỘ lịch sử có thể (không giới hạn ngày) để dashboard lọc theo bất kỳ khoảng
thời gian nào (7 ngày / 30 ngày / theo năm...) mà không cần gọi lại API mỗi lần đổi filter.

Sapo: get_orders(days=None) lấy hết toàn bộ đơn hàng từ trước đến nay.
Meta: get_ads_spend*(days=None) dùng date_preset="maximum" (Meta thường giữ tối đa ~37
tháng dữ liệu insights - đây là giới hạn của Meta, không phải giới hạn code).
"""

import json
import datetime as dt
from pathlib import Path

from business_dashboard_sapo import get_orders, get_variant_sku_map
from business_dashboard_costs import load_cost_map
from business_dashboard_meta import get_ads_spend, get_ads_spend_daily
from business_dashboard_settlement import load_settlement_fees
from business_dashboard_aggregate import build_summary, build_product_breakdown, build_daily_summary

OUT_PATH = Path(__file__).resolve().parent / "data.json"


def main():
    orders = get_orders(days=None)
    variant_sku_map = get_variant_sku_map()
    cost_map = load_cost_map()
    ads_data = get_ads_spend(days=None)
    ads_daily = get_ads_spend_daily(days=None)
    settlement_df = load_settlement_fees()

    summary = build_summary(orders, variant_sku_map, cost_map, ads_data, settlement_df)
    product_breakdown = build_product_breakdown(orders, variant_sku_map, cost_map)
    daily_summary = build_daily_summary(orders, variant_sku_map, cost_map, settlement_df)

    payload = {
        "updated_at": dt.datetime.now().isoformat(),
        "total_ads_spend": ads_data.get("total_spend", 0),
        "ads_note": "Ads spend hiện là TỔNG chung (Meta Ads), chưa gán theo kênh cụ thể.",
        "channels": summary.to_dict(orient="records"),
        "product_breakdown": product_breakdown.to_dict(orient="records"),
        "daily": daily_summary.to_dict(orient="records"),
        "ads_daily": ads_daily,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {OUT_PATH}")
    print(f"Lấy TOÀN BỘ lịch sử — {len(orders)} đơn hàng.")
    print(f"Tổng ads spend (Meta, chưa gán kênh): {ads_data.get('total_spend', 0):,.0f}đ")
    print(f"Số SKU có giá vốn trong file: {len(cost_map)}")
    print(f"Số dòng breakdown theo sản phẩm x kênh x shop/page: {len(product_breakdown)}")
    print(f"Số dòng dữ liệu theo ngày x kênh x shop/page: {len(daily_summary)}")
    print(f"Số ngày có dữ liệu ads: {len(ads_daily)}")
    print(f"Số tổ hợp channel x shop/page nhận diện được: {len(summary)}")


if __name__ == "__main__":
    main()
