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

    print("\n=== Quét nhiều trang PRODUCT - tìm field giá vốn + tìm sản phẩm COMBO ===")
    cost_like_keywords = ["cost", "von", "gia_von", "purchase", "import", "avg"]
    combo_like_keywords = ["combo", "component", "bundle", "child", "item_id", "included"]
    found_cost = False
    found_combo_product = None
    variant_types = set()

    page = 1
    total_scanned = 0
    while True:  # quét hết toàn bộ sản phẩm (hiện ~501, tương lai tới ~2000+)
        r2 = requests.get(f"{base}/products.json", auth=auth, params={"limit": 250, "page": page}, timeout=30)
        r2.raise_for_status()
        products = r2.json().get("products", [])
        if not products:
            break
        total_scanned += len(products)
        for p in products:
            # field lạ ở cấp product (ngoài id/name/variants) có thể liên quan tới combo
            product_extra_keys = {k: v for k, v in p.items() if k not in ("id", "name", "variants")}
            for k in product_extra_keys:
                if any(kw in k.lower() for kw in combo_like_keywords) and found_combo_product is None:
                    found_combo_product = p

            for v in p.get("variants", []):
                variant_types.add(v.get("type"))
                cost_fields = {k: val for k, val in v.items() if any(kw in k.lower() for kw in cost_like_keywords)}
                if cost_fields:
                    found_cost = True
                    print(f"[CÓ GIÁ VỐN] '{p.get('name')}' (product_id={p.get('id')}, sku={v.get('sku')}, type={v.get('type')}): {cost_fields}")
                if v.get("type") and v.get("type") != "normal" and found_combo_product is None:
                    found_combo_product = p

        if len(products) < 250:
            break  # trang cuối
        page += 1
        if page > 20:  # phòng hờ vòng lặp vô hạn, đủ cho ~5000 sản phẩm
            break

    print(f"\nTổng số sản phẩm đã quét: {total_scanned}")
    print(f"Các giá trị 'type' của variant gặp phải: {variant_types}")
    if not found_cost:
        print("Không tìm thấy field giá vốn nào trong các trang đã quét.")
    if found_combo_product:
        print("\n=== 1 SẢN PHẨM COMBO MẪU (toàn bộ field) ===")
        print(json.dumps(found_combo_product, ensure_ascii=False, indent=2))
    else:
        print("\nChưa gặp sản phẩm nào có type khác 'normal' hoặc field liên quan combo trong các trang đã quét.")


if __name__ == "__main__":
    main()
