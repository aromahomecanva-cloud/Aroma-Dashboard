"""
Giống business_dashboard_main.py, nhưng xuất thêm data.json (dùng cho GitHub Actions
đẩy dữ liệu về, để Claude đọc và cập nhật dashboard artifact).
"""

import json
import datetime as dt
from pathlib import Path

from business_dashboard_sapo import get_orders, get_variant_sku_map
from business_dashboard_costs import load_cost_map
from business_dashboard_meta import get_ads_spend
from business_dashboard_settlement import load_settlement_fees
from business_dashboard_aggregate import build_summary

OUT_PATH = Path(__file__).resolve().parent / "data.json"


def main():
    orders = get_orders(days=30)
    variant_sku_map = get_variant_sku_map()
    cost_map = load_cost_map()
    ads_data = get_ads_spend(days=30)
    settlement_df = load_settlement_fees()
    summary = build_summary(orders, variant_sku_map, cost_map, ads_data, settlement_df)

    payload = {
        "updated_at": dt.datetime.now().isoformat(),
        "total_ads_spend": ads_data.get("total_spend", 0),
        "ads_note": "Ads spend hiện là TỔNG chung (Meta Ads), chưa gán theo kênh cụ thể.",
        "channels": summary.to_dict(orient="records"),
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {OUT_PATH}")
    print(f"Tổng ads spend (Meta, chưa gán kênh): {ads_data.get('total_spend', 0):,.0f}đ")
    print(f"Số SKU có giá vốn trong file: {len(cost_map)}")


if __name__ == "__main__":
    main()
