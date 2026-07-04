"""
Script CHẨN ĐOÁN: so sánh doanh thu tự tính (theo business_dashboard_revenue.py) với số liệu
THẬT lấy trực tiếp từ file "Báo cáo doanh thu theo thời gian" mà user đã xuất từ Sapo
(xuat_file_bao_cao_doanh_thu_theo_thoi_gian_04-07-2026_11-17.xls, giữ lại 30 ngày
2026-06-01 -> 2026-06-30, TOÀN SHOP không tách kênh).

Mục đích: xác nhận công thức trong business_dashboard_revenue.py (total_line_items_price -
total_discounts - refund_value = Doanh thu thuần, loại đơn huỷ) có thực sự khớp 100% với
số Sapo tự tính hay không, TRƯỚC KHI tin tưởng dùng số này làm "DT gộp" chính thức trên
dashboard. Kết quả được ghi vào data.json (mục debug_revenue_check) để Claude đọc qua
`git clone` mà không cần user copy/paste log tay.
"""

from business_dashboard_revenue import filter_valid_orders, order_revenue_breakdown

# Ground truth lấy trực tiếp từ file Sapo export (xem docstring trên).
GROUND_TRUTH = [
    {"date": "2026-06-30", "orders": 107, "item_revenue": 30514250.0, "discount": 6442316.0, "refund_value": 2781460.0, "net_revenue": 21290474.0, "shipping_fee": 35000.0, "total_revenue": 21325474.0},
    {"date": "2026-06-29", "orders": 65, "item_revenue": 20052000.0, "discount": 3890679.0, "refund_value": 2076980.0, "net_revenue": 14084341.0, "shipping_fee": 120000.0, "total_revenue": 14204341.0},
    {"date": "2026-06-28", "orders": 57, "item_revenue": 17517000.0, "discount": 3810862.0, "refund_value": 1428011.0, "net_revenue": 12278127.0, "shipping_fee": 120000.0, "total_revenue": 12398127.0},
    {"date": "2026-06-27", "orders": 97, "item_revenue": 34741800.0, "discount": 8231331.0, "refund_value": 3038880.0, "net_revenue": 23471589.0, "shipping_fee": 75000.0, "total_revenue": 23546589.0},
    {"date": "2026-06-26", "orders": 110, "item_revenue": 42473400.0, "discount": 8328361.0, "refund_value": 4879732.0, "net_revenue": 29265307.0, "shipping_fee": 60000.0, "total_revenue": 29325307.0},
    {"date": "2026-06-25", "orders": 124, "item_revenue": 43619600.0, "discount": 9955102.0, "refund_value": 5506100.0, "net_revenue": 28158398.0, "shipping_fee": 145000.0, "total_revenue": 28303398.0},
    {"date": "2026-06-24", "orders": 107, "item_revenue": 34413000.0, "discount": 7383802.0, "refund_value": 2035366.0, "net_revenue": 24993832.0, "shipping_fee": 70000.0, "total_revenue": 25063832.0},
    {"date": "2026-06-23", "orders": 98, "item_revenue": 35003900.0, "discount": 8158619.0, "refund_value": 3665380.0, "net_revenue": 23179901.0, "shipping_fee": 0.0, "total_revenue": 23179901.0},
    {"date": "2026-06-22", "orders": 99, "item_revenue": 36658360.0, "discount": 7458925.0, "refund_value": 1293621.0, "net_revenue": 27905814.0, "shipping_fee": 25000.0, "total_revenue": 27930814.0},
    {"date": "2026-06-21", "orders": 85, "item_revenue": 25386000.0, "discount": 5147285.0, "refund_value": 1772328.0, "net_revenue": 18466387.0, "shipping_fee": 60000.0, "total_revenue": 18526387.0},
    {"date": "2026-06-20", "orders": 92, "item_revenue": 27491000.0, "discount": 5958873.0, "refund_value": 2718116.0, "net_revenue": 18814011.0, "shipping_fee": 35000.0, "total_revenue": 18849011.0},
    {"date": "2026-06-19", "orders": 93, "item_revenue": 31463750.0, "discount": 5417623.0, "refund_value": 2828131.0, "net_revenue": 23217996.0, "shipping_fee": 30000.0, "total_revenue": 23247996.0},
    {"date": "2026-06-18", "orders": 90, "item_revenue": 28170200.0, "discount": 6319585.0, "refund_value": 3714491.0, "net_revenue": 18136124.0, "shipping_fee": 85000.0, "total_revenue": 18221124.0},
    {"date": "2026-06-17", "orders": 106, "item_revenue": 30735000.0, "discount": 5962142.0, "refund_value": 3777857.0, "net_revenue": 20995001.0, "shipping_fee": 370000.0, "total_revenue": 21365001.0},
    {"date": "2026-06-16", "orders": 105, "item_revenue": 28190250.0, "discount": 6261267.0, "refund_value": 1856260.0, "net_revenue": 20072723.0, "shipping_fee": 25000.0, "total_revenue": 20097723.0},
    {"date": "2026-06-15", "orders": 87, "item_revenue": 34196000.0, "discount": 8589233.0, "refund_value": 3995132.0, "net_revenue": 21611635.0, "shipping_fee": 150000.0, "total_revenue": 21761635.0},
    {"date": "2026-06-14", "orders": 77, "item_revenue": 26425000.0, "discount": 5128545.0, "refund_value": 2505968.0, "net_revenue": 18790487.0, "shipping_fee": 140000.0, "total_revenue": 18930487.0},
    {"date": "2026-06-13", "orders": 58, "item_revenue": 15780000.0, "discount": 3692296.0, "refund_value": 719900.0, "net_revenue": 11367804.0, "shipping_fee": 35000.0, "total_revenue": 11402804.0},
    {"date": "2026-06-12", "orders": 67, "item_revenue": 28370240.0, "discount": 5606236.0, "refund_value": 1218818.0, "net_revenue": 21545186.0, "shipping_fee": 70000.0, "total_revenue": 21615186.0},
    {"date": "2026-06-11", "orders": 83, "item_revenue": 27507000.0, "discount": 6261029.0, "refund_value": 1501049.0, "net_revenue": 19744922.0, "shipping_fee": 35000.0, "total_revenue": 19779922.0},
    {"date": "2026-06-10", "orders": 94, "item_revenue": 42570000.0, "discount": 10305606.0, "refund_value": 1288150.0, "net_revenue": 30976244.0, "shipping_fee": 110000.0, "total_revenue": 31086244.0},
    {"date": "2026-06-09", "orders": 60, "item_revenue": 25706000.0, "discount": 4681177.0, "refund_value": 7472988.0, "net_revenue": 13551835.0, "shipping_fee": 35000.0, "total_revenue": 13586835.0},
    {"date": "2026-06-08", "orders": 63, "item_revenue": 28904800.0, "discount": 6335471.0, "refund_value": 1184900.0, "net_revenue": 21384429.0, "shipping_fee": 0.0, "total_revenue": 21384429.0},
    {"date": "2026-06-07", "orders": 69, "item_revenue": 19145000.0, "discount": 4604940.0, "refund_value": 2163369.0, "net_revenue": 12376691.0, "shipping_fee": 0.0, "total_revenue": 12376691.0},
    {"date": "2026-06-06", "orders": 109, "item_revenue": 33610000.0, "discount": 8902899.0, "refund_value": 3233006.0, "net_revenue": 21474095.0, "shipping_fee": 105000.0, "total_revenue": 21579095.0},
    {"date": "2026-06-05", "orders": 66, "item_revenue": 20567000.0, "discount": 4552715.0, "refund_value": 3094874.0, "net_revenue": 12919411.0, "shipping_fee": 105000.0, "total_revenue": 13024411.0},
    {"date": "2026-06-04", "orders": 45, "item_revenue": 15743000.0, "discount": 3726472.0, "refund_value": 768112.0, "net_revenue": 11248416.0, "shipping_fee": 90000.0, "total_revenue": 11338416.0},
    {"date": "2026-06-03", "orders": 84, "item_revenue": 30457000.0, "discount": 6590515.0, "refund_value": 2944730.0, "net_revenue": 20921755.0, "shipping_fee": 120000.0, "total_revenue": 21041755.0},
    {"date": "2026-06-02", "orders": 69, "item_revenue": 21542000.0, "discount": 4941803.0, "refund_value": 1178000.0, "net_revenue": 15422197.0, "shipping_fee": 0.0, "total_revenue": 15422197.0},
    {"date": "2026-06-01", "orders": 71, "item_revenue": 21940000.0, "discount": 4443458.0, "refund_value": 1900030.0, "net_revenue": 15596512.0, "shipping_fee": 110000.0, "total_revenue": 15706512.0},
]


def _order_date(o: dict) -> str:
    created = o.get("created_on") or ""
    return str(created)[:10]


def run_check(orders_raw: list) -> dict:
    """
    orders_raw: TOÀN BỘ orders lấy từ Sapo (CHƯA lọc huỷ) — hàm này tự lọc bên trong để so
    sánh cả 2 trường hợp (trước/sau khi loại đơn huỷ) giúp thấy rõ tác động của việc lọc.
    """
    valid_orders = filter_valid_orders(orders_raw)
    cancelled_count = len(orders_raw) - len(valid_orders)

    by_date = {}
    for o in valid_orders:
        d = _order_date(o)
        b = by_date.setdefault(d, {"orders": 0, "item_revenue": 0.0, "discount": 0.0,
                                    "refund_value": 0.0, "net_revenue": 0.0, "shipping_fee": 0.0,
                                    "total_revenue": 0.0})
        rev = order_revenue_breakdown(o)
        b["orders"] += 1
        b["item_revenue"] += rev["item_revenue"]
        b["discount"] += rev["discount"]
        b["refund_value"] += rev["refund_value"]
        b["net_revenue"] += rev["net_revenue"]
        b["shipping_fee"] += rev["shipping_fee"]
        b["total_revenue"] += rev["total_revenue"]

    rows = []
    total_abs_diff_net = 0.0
    total_truth_net = 0.0
    exact_matches = 0
    for truth in GROUND_TRUTH:
        d = truth["date"]
        computed = by_date.get(d, {"orders": 0, "item_revenue": 0.0, "discount": 0.0,
                                    "refund_value": 0.0, "net_revenue": 0.0, "shipping_fee": 0.0,
                                    "total_revenue": 0.0})
        diff_net = round(computed["net_revenue"] - truth["net_revenue"], 2)
        diff_orders = computed["orders"] - truth["orders"]
        total_abs_diff_net += abs(diff_net)
        total_truth_net += truth["net_revenue"]
        if abs(diff_net) < 1 and diff_orders == 0:
            exact_matches += 1
        rows.append({
            "date": d,
            "truth": truth,
            "computed": {k: round(v, 2) for k, v in computed.items()},
            "diff_orders": diff_orders,
            "diff_net_revenue": diff_net,
        })

    return {
        "cancelled_orders_excluded": cancelled_count,
        "valid_orders_total": len(valid_orders),
        "days_checked": len(GROUND_TRUTH),
        "days_exact_match": exact_matches,
        "total_abs_diff_net_revenue": round(total_abs_diff_net, 2),
        "total_truth_net_revenue": round(total_truth_net, 2),
        "pct_diff": round(total_abs_diff_net / total_truth_net * 100, 3) if total_truth_net else None,
        "rows": rows,
    }
