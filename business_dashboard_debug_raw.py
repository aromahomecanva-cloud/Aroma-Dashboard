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
        keep_keys = ["id", "name", "created_on", "total_price", "source_name", "tags", "note", "line_items", "financial_status"]
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

    # Thử endpoint CHI TIẾT 1 sản phẩm (khác endpoint danh sách) xem có field giá vốn không
    if products:
        pid = products[0]["id"]
        print(f"\n=== Thử endpoint chi tiết /admin/products/{{id}}.json cho product_id={pid} ===")
        try:
            r3 = requests.get(f"{base}/products/{pid}.json", auth=auth, timeout=30)
            r3.raise_for_status()
            detail = r3.json().get("product", r3.json())
            print(json.dumps(detail, ensure_ascii=False, indent=2)[:3000])
        except Exception as e:
            print(f"Lỗi khi gọi endpoint chi tiết: {e}")

    # Thử endpoint inventory / variant riêng lẻ
    print("\n=== Thử endpoint /admin/variants/{id}.json (nếu tồn tại) ===")
    try:
        first_variant_id = None
        for p in products:
            for v in p.get("variants", []):
                first_variant_id = v.get("id")
                break
            if first_variant_id:
                break
        if first_variant_id:
            r4 = requests.get(f"{base}/variants/{first_variant_id}.json", auth=auth, timeout=30)
            print(f"Status: {r4.status_code}")
            print(r4.text[:2000])
    except Exception as e:
        print(f"Lỗi khi gọi endpoint variant: {e}")
    print(f"Các giá trị 'type' của variant gặp phải: {variant_types}")
    if not found_cost:
        print("Không tìm thấy field giá vốn nào trong các trang đã quét.")
    if found_combo_product:
        print("\n=== 1 SẢN PHẨM COMBO MẪU (toàn bộ field) ===")
        print(json.dumps(found_combo_product, ensure_ascii=False, indent=2))
    else:
        print("\nChưa gặp sản phẩm nào có type khác 'normal' hoặc field liên quan combo trong các trang đã quét.")


def debug_cost_files():
    from business_dashboard_costs import EXPORT_DIR, _load_regular_costs, _load_combo_bom, load_cost_map
    print("\n=== Kiểm tra thư mục product_exports/ ===")
    print("EXPORT_DIR:", EXPORT_DIR, "- tồn tại:", EXPORT_DIR.exists())
    if EXPORT_DIR.exists():
        for f in sorted(EXPORT_DIR.iterdir()):
            print(f"  - {f.name} ({f.stat().st_size} bytes)")

    regular = _load_regular_costs()
    print(f"\nSố SKU đọc được từ file products_export: {len(regular)}")
    if regular:
        sample = list(regular.items())[:3]
        print("Mẫu:", sample)

    bom = _load_combo_bom()
    print(f"\nSố combo đọc được từ file combos_export: {len(bom)}")
    if bom:
        sample_combo = list(bom.items())[0]
        print("Mẫu combo:", sample_combo)

    final = load_cost_map()
    print(f"\nTổng SKU trong cost_map cuối cùng: {len(final)}")


def debug_order_full_fields():
    """
    Tìm field nhận diện SHOP/PAGE cụ thể (không chỉ source_name chung chung),
    và field discount/voucher đã có sẵn trong Order API (không cần file đối soát).
    Lấy 1 order mẫu từ vài kênh khác nhau (shopee, facebook, pos) để so sánh.
    """
    base = f"https://{Config.SAPO_STORE}.mysapo.net/admin"
    auth = (Config.SAPO_API_KEY, Config.SAPO_API_SECRET)

    print("\n\n" + "=" * 60)
    print("=== TÌM FIELD SHOP/PAGE + DISCOUNT/VOUCHER TRONG ORDER ===")
    print("=" * 60)

    seen_channels = {}
    page = 1
    while len(seen_channels) < 5 and page <= 10:
        resp = requests.get(f"{base}/orders.json", auth=auth, params={"page": page, "limit": 250}, timeout=30)
        resp.raise_for_status()
        batch = resp.json().get("orders", [])
        if not batch:
            break
        for o in batch:
            ch = o.get("source_name")
            if ch not in seen_channels:
                seen_channels[ch] = o
        page += 1

    discount_keywords = ["discount", "voucher", "coupon", "aff", "commission", "promo"]
    shop_keywords = ["location", "shop", "page", "channel_id", "referring", "landing", "pos_", "store"]

    for ch, o in seen_channels.items():
        print(f"\n--- Kênh: {ch} (order id={o.get('id')}) ---")
        print(f"tags: {o.get('tags')!r}")
        print(f"note: {o.get('note')!r}")
        interesting = {k: v for k, v in o.items() if any(kw in k.lower() for kw in discount_keywords + shop_keywords)}
        if interesting:
            print(json.dumps(interesting, ensure_ascii=False, indent=2))
        else:
            print("Không thấy field discount/voucher/shop/page nào rõ ràng trong order này.")
        print("Toàn bộ field top-level của order này:", list(o.keys()))

    # Kiểm tra riêng field "tags" trên NHIỀU order hơn (không chỉ 1 order/kênh),
    # để xem tags có thật sự chứa tên shop/page cụ thể hay không (vd: "Fanpage A", "Shop B"...)
    print("\n--- Mẫu 'tags' từ 20 order đầu tiên (bất kỳ kênh nào) ---")
    resp = requests.get(f"{base}/orders.json", auth=auth, params={"page": 1, "limit": 20}, timeout=30)
    resp.raise_for_status()
    sample_orders = resp.json().get("orders", [])
    for o in sample_orders:
        print(f"  id={o.get('id')} source_name={o.get('source_name')} tags={o.get('tags')!r}")


if __name__ == "__main__":
    main()
    debug_cost_files()
    debug_order_full_fields()
