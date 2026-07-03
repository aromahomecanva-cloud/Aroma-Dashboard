"""
Giống business_dashboard_main.py, nhưng xuất thêm data.json (dùng cho GitHub Actions
đẩy dữ liệu về, để Claude đọc và cập nhật dashboard artifact).
"""

import json
import datetime as dt
from pathlib import Path

from business_dashboard_sapo import get_orders, get_product_costs
from business_dashboard_meta import get_ads_spend
from business_dashboard_settlement import load_settlement_fees
from business_dashboard_aggregate import build_summary

OUT_PATH = Path(__file__).resolve().parent / "data.json"


def main():
    orders = get_orders(days=30)
    product_costs = get_product_costs()
    ads_data = get_ads_spend(days=30)
    settlement_df = load_settlement_fees()
    summary = build_summary(orders, product_costs, ads_data, settlement_df)

    payload = {
        "updated_at": dt.datetime.now().isoformat(),
        "channels": summary.to_dict(orient="records"),
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {OUT_PATH}")


if __name__ == "__main__":
    main()
