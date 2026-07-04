"""
Tính DOANH THU THUẦN theo ĐÚNG công thức Sapo dùng trong báo cáo "Doanh thu theo thời gian"
(Sapo Admin -> Báo cáo -> Doanh thu theo thời gian), dựa trên field chính thức của Order API
(https://support.sapo.vn/cac-thuoc-tinh-cua-order-api):

  Tiền hàng           = total_line_items_price (tổng giá trị các line item, TRƯỚC giảm giá)
  Giảm giá            = total_discounts (tổng giảm giá áp dụng cho toàn đơn)
  Tiền hàng trả lại   = tổng (price * quantity) của các refund_line_items trong "refunds"
                        (Sapo Refund API: mỗi refund có refund_line_items, mỗi item có
                        line_item.price + quantity đã hoàn)
  Doanh thu thuần     = Tiền hàng - Giảm giá - Tiền hàng trả lại
  Phí giao hàng       = tổng price của shipping_lines
  Tổng doanh thu      = Doanh thu thuần + Phí giao hàng + Tiền thuế

QUAN TRỌNG: đơn đã HUỶ (status="cancelled" hoặc có cancelled_on) bị loại hẳn khỏi MỌI thống
kê doanh thu — đúng như user xác nhận "số liệu của sapo đã loại trừ hết những đơn hoàn hủy".
Dùng filter_valid_orders() ngay sau khi lấy orders từ Sapo, TRƯỚC KHI đưa vào bất kỳ hàm
build_* nào trong business_dashboard_aggregate.py.

Trước đây dashboard dùng trực tiếp field "total_price" (tổng tiền khách phải trả, ĐÃ gồm
phí ship/thuế, và KHÔNG trừ giá trị hàng trả lại, KHÔNG loại đơn huỷ) làm "DT gộp" -> lệch
khá xa so với "Doanh thu thuần" thật của Sapo (đã xác nhận qua file
"Báo cáo doanh thu theo thời gian" user xuất trực tiếp từ Sapo).
"""


def is_cancelled(o: dict) -> bool:
    """Đơn bị huỷ: status == 'cancelled' HOẶC có cancelled_on (khác null/rỗng)."""
    if o.get("status") == "cancelled":
        return True
    if o.get("cancelled_on"):
        return True
    return False


def filter_valid_orders(orders: list) -> list:
    """Loại bỏ TOÀN BỘ đơn đã huỷ khỏi danh sách orders — áp dụng 1 LẦN DUY NHẤT ngay sau khi
    lấy orders từ Sapo (business_dashboard_export_json.py), để mọi hàm build_*/diagnostics
    phía sau đều nhất quán dùng chung 1 tập order "hợp lệ"."""
    return [o for o in orders if not is_cancelled(o)]


def _to_float(v) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def order_revenue_breakdown(o: dict) -> dict:
    """
    Trả về dict các thành phần doanh thu của 1 order, theo đúng công thức báo cáo Sapo
    "Doanh thu theo thời gian" (xem docstring module):
      item_revenue, discount, refund_value, shipping_fee, tax, net_revenue, total_revenue
    """
    item_revenue = _to_float(o.get("total_line_items_price"))
    discount = _to_float(o.get("total_discounts"))

    refund_value = 0.0
    for rf in (o.get("refunds") or []):
        for rli in (rf.get("refund_line_items") or []):
            li = rli.get("line_item") or {}
            price = _to_float(li.get("price"))
            qty = _to_float(rli.get("quantity"))
            refund_value += price * qty

    shipping_fee = sum(_to_float(sl.get("price")) for sl in (o.get("shipping_lines") or []))

    # Sapo Order API (theo tài liệu chính thức) không thấy field thuế cấp order rõ ràng
    # (không có total_tax) -> để 0 (thực tế cột "Tiền thuế" trong báo cáo Sapo cũng luôn = 0
    # đối với dữ liệu của shop này, xem file "Báo cáo doanh thu theo thời gian" đã đối chiếu).
    tax = 0.0

    net_revenue = item_revenue - discount - refund_value
    total_revenue = net_revenue + shipping_fee + tax

    return {
        "item_revenue": item_revenue,
        "discount": discount,
        "refund_value": refund_value,
        "shipping_fee": shipping_fee,
        "tax": tax,
        "net_revenue": net_revenue,
        "total_revenue": total_revenue,
    }
