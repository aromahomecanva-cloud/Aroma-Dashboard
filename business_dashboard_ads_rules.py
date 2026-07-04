"""
Khung RULE cảnh báo ads/ad set "vi phạm" (chi tiêu kém hiệu quả) — theo yêu cầu user:
"để tôi tự nhập con số cụ thể sau" -> file này chỉ dựng SẴN cấu trúc rule linh hoạt, tất cả
ngưỡng mặc định để None (nghĩa là CHƯA BẬT), user tự điền số cụ thể vào RULES_CONFIG bên dưới
khi đã sẵn sàng. Rule nào có giá trị None sẽ bị evaluate_rules() bỏ qua, không áp dụng.

Cách dùng:
1. Sửa các giá trị trong RULES_CONFIG (đổi None -> số cụ thể) theo đúng đơn vị ghi chú.
2. Gọi evaluate_rules(ads) với `ads` là list lấy từ business_dashboard_meta.get_ads_detail()
   (có thể truyền d["ads"] để check từng ad, hoặc d["adsets"] để check từng ad set).
3. Kết quả trả về là list các dòng VI PHẠM ít nhất 1 rule, kèm "violation_reasons" (list mô tả
   lý do bằng tiếng Việt) để hiển thị lên dashboard hoặc gửi cảnh báo (kênh gửi sẽ làm ở bước
   sau — hiện tại mới dừng ở việc tính toán, chưa nối vào kênh gửi tin nhắn nào).
"""

# Tất cả để None = CHƯA áp dụng rule đó. Điền số cụ thể (đơn vị VNĐ / % ghi chú kèm theo).
RULES_CONFIG = {
    # CPC (chi phí mỗi lượt click) vượt ngưỡng này (đơn vị: VNĐ/click) -> cảnh báo.
    "cpc_max": None,

    # CPA (chi phí mỗi "kết quả" - xem ghi chú "results" trong business_dashboard_meta.py)
    # vượt ngưỡng này (đơn vị: VNĐ/kết quả) -> cảnh báo.
    "cpa_max": None,

    # CTR (tỷ lệ click) THẤP HƠN ngưỡng này (đơn vị: %) -> cảnh báo quảng cáo kém thu hút.
    "ctr_min": None,

    # Đã chi tiêu vượt ngưỡng này (đơn vị: VNĐ) MÀ chưa có "kết quả" nào (results == 0)
    # -> cảnh báo tốn tiền không hiệu quả.
    "spend_no_result_threshold": None,
}


def _fmt_vnd(n) -> str:
    return f"{n:,.0f}".replace(",", ".") + "đ"


def evaluate_rules(ads: list, rules: dict | None = None) -> list:
    """
    Kiểm tra từng dòng trong `ads` (list dict có ít nhất: spend, clicks, impressions, ctr,
    cpc, results) theo RULES_CONFIG (hoặc `rules` truyền vào để override, VD test thử ngưỡng
    khác mà không sửa file). Chỉ áp dụng những rule có giá trị KHÁC None.

    Trả về list các dòng ban đầu (giữ nguyên mọi field cũ) + thêm field "violation_reasons"
    (list các câu mô tả lý do vi phạm bằng tiếng Việt) — CHỈ những dòng vi phạm ít nhất 1 rule
    mới được trả về (không trả toàn bộ list gốc).
    """
    rules = rules if rules is not None else RULES_CONFIG
    violations = []

    for row in ads:
        reasons = []
        spend = row.get("spend") or 0
        clicks = row.get("clicks") or 0
        ctr = row.get("ctr") or 0
        cpc = row.get("cpc") or 0
        results = row.get("results") or 0
        cpa = row.get("cpa")
        if cpa is None and results:
            cpa = spend / results

        if rules.get("cpc_max") is not None and clicks > 0 and cpc > rules["cpc_max"]:
            reasons.append(f"CPC {_fmt_vnd(cpc)} > ngưỡng {_fmt_vnd(rules['cpc_max'])}")

        if rules.get("cpa_max") is not None and cpa is not None and cpa > rules["cpa_max"]:
            reasons.append(f"CPA {_fmt_vnd(cpa)} > ngưỡng {_fmt_vnd(rules['cpa_max'])}")

        if rules.get("ctr_min") is not None and (row.get("impressions") or 0) > 0 and ctr < rules["ctr_min"]:
            reasons.append(f"CTR {ctr:.2f}% < ngưỡng {rules['ctr_min']:.2f}%")

        if (
            rules.get("spend_no_result_threshold") is not None
            and results == 0
            and spend > rules["spend_no_result_threshold"]
        ):
            reasons.append(f"Đã chi {_fmt_vnd(spend)} nhưng chưa có kết quả nào")

        if reasons:
            violations.append({**row, "violation_reasons": reasons})

    return violations


def any_rule_active(rules: dict | None = None) -> bool:
    """True nếu có ít nhất 1 rule đã được set (khác None) — dùng để dashboard biết có nên
    hiển thị mục cảnh báo hay không (tránh hiện mục trống khi user chưa set gì)."""
    rules = rules if rules is not None else RULES_CONFIG
    return any(v is not None for v in rules.values())


if __name__ == "__main__":
    # Test nhanh với vài ngưỡng giả định (không sửa RULES_CONFIG thật) để xem cách dùng.
    sample_ads = [
        {"ad_id": "A1", "ad_name": "Ad tốt", "spend": 500000, "clicks": 200, "impressions": 10000,
         "ctr": 2.0, "cpc": 2500, "results": 10, "cpa": 50000},
        {"ad_id": "A2", "ad_name": "Ad tốn tiền không hiệu quả", "spend": 3000000, "clicks": 50,
         "impressions": 20000, "ctr": 0.25, "cpc": 60000, "results": 0, "cpa": None},
    ]
    test_rules = {"cpc_max": 10000, "cpa_max": None, "ctr_min": 0.5, "spend_no_result_threshold": 1000000}
    for v in evaluate_rules(sample_ads, test_rules):
        print(v["ad_name"], "->", v["violation_reasons"])
