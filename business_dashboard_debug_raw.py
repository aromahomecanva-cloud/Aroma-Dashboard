"""
Script chẩn đoán: in ra cấu trúc THẬT của 1 order và 1 product từ Sapo,
để chỉnh lại đúng tên field cho việc tính COGS.
Không in thông tin khách hàng/địa chỉ nhạy cảm nếu có (chỉ in field liên quan giá/sản phẩm).
"""

import json
import requests
from business_dashboard_config import Config


def main():
    base = f"https://{Config.SAPO_STORE}.mysapo.net/admin"
    auth = (Config.SAPO_API_KEY, Config.SAPO_API_SECRET)

    print("=== 1 ORDER MẪU (rút gọn) ===")
    r = requests.get(f"{base}/orders.json", auth=auth, params={"limit": 1}, timeout=30)
    r.raise_for_status()
    orders = r.json().get("orders", [])
    if orders:
        o = orders[0]
        keep_keys = ["id", "name", "created_on", "total_price", "source_name", "line_items", "financial_status"]
        slim = {k: o.get(k) for k in keep_keys if k in o}
        # rút gọn line_items chỉ lấy vài field
        if "line_items" in slim:
            slim["line_items"] = [
                {k: li.get(k) for k in ["id", "product_id", "variant_id", "quantity", "price", "title"] if k in li}
                for li in slim["line_items"]
            ]
        print(json.dumps(slim, ensure_ascii=False, indent=2))
    else:
        print("Không lấy được order nào.")

    print("\n=== 1 PRODUCT MẪU (rút gọn) ===")
    r2 = requests.get(f"{base}/products.json", auth=auth, params={"limit": 1}, timeout=30)
    r2.raise_for_status()
    products = r2.json().get("products", [])
    if products:
        p = products[0]
        keep_keys = ["id", "name", "variants"]
        slim = {k: p.get(k) for k in keep_keys if k in p}
        print(json.dumps(slim, ensure_ascii=False, indent=2))
    else:
        print("Không lấy được product nào.")


if __name__ == "__main__":
    main()
