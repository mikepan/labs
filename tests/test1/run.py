import csv
from datetime import datetime
import os
import re
import sys
from collections import defaultdict

from tests.framework import Step, Test, git_changes, custom_check, CustomAssert

_csv_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookstore_orders.csv")


def _norm(s: str) -> str:
    return re.sub(r"[_\s\-]+", "", str(s).strip().lower())


def _clean_float(val: str) -> float:
    cleaned = re.sub(r"[^\d\.\-]", "", str(val).strip())
    return float(cleaned)


# ==============================================================================
# Ground Truth Computation Functions
# ==============================================================================

def compute_expected_top_customers(source_csv_path: str, top_n: int = 10) -> list[tuple[str, float]]:
    """Parse bookstore orders CSV to compute top customers by net spend,
    aggregating by unique customer_id to properly distinguish different customers who share names."""
    customer_spends = defaultdict(lambda: {"fullname": "", "total_spend": 0.0})
    with open(source_csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cust_id = row["customer_id"].strip()
            first_name = row["customer_first_name"].strip()
            last_name = row["customer_last_name"].strip()
            fullname = f"{first_name} {last_name}"
            grand_total = float(row["order_grand_total"]) if row.get("order_grand_total") else 0.0
            refund = float(row["refund_amount"]) if row.get("refund_amount") else 0.0
            net_spend = grand_total - refund

            customer_spends[cust_id]["fullname"] = fullname
            customer_spends[cust_id]["total_spend"] += net_spend

    sorted_customers = sorted(customer_spends.values(), key=lambda c: c["total_spend"], reverse=True)[:top_n]
    return [(c["fullname"], c["total_spend"]) for c in sorted_customers]


def compute_expected_top_authors(source_csv_path: str, top_n: int = 10) -> list[tuple[str, int, float]]:
    """Compute top authors by gross sales across both primary and secondary items."""
    author_stats = defaultdict(lambda: {"units": 0, "gross_sales": 0.0})
    with open(source_csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            p_auth = row.get("primary_item_author", "").strip()
            p_qty = int(row["primary_item_quantity"]) if row.get("primary_item_quantity") else 0
            p_price = float(row["primary_item_unit_price"]) if row.get("primary_item_unit_price") else 0.0
            if p_auth:
                author_stats[p_auth]["units"] += p_qty
                author_stats[p_auth]["gross_sales"] += p_qty * p_price

            s_auth = row.get("secondary_item_author", "").strip()
            s_qty = int(row["secondary_item_quantity"]) if row.get("secondary_item_quantity") else 0
            s_price = float(row["secondary_item_unit_price"]) if row.get("secondary_item_unit_price") else 0.0
            if s_auth:
                author_stats[s_auth]["units"] += s_qty
                author_stats[s_auth]["gross_sales"] += s_qty * s_price

    sorted_authors = sorted(author_stats.items(), key=lambda x: x[1]["gross_sales"], reverse=True)[:top_n]
    return [(author, data["units"], data["gross_sales"]) for author, data in sorted_authors]


def compute_expected_genre_returns(source_csv_path: str) -> list[tuple[str, int, int, float, float]]:
    """Compute order return rate percentage and total refunds per book genre."""
    genre_stats = defaultdict(lambda: {"total": 0, "returned": 0, "refund": 0.0})
    with open(source_csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            genre = row.get("primary_item_genre", "").strip()
            if not genre:
                continue
            is_returned = row.get("return_requested", "").strip().lower() == "yes"
            refund = float(row.get("refund_amount") or 0.0)
            genre_stats[genre]["total"] += 1
            if is_returned:
                genre_stats[genre]["returned"] += 1
            genre_stats[genre]["refund"] += refund

    results = []
    for genre, data in genre_stats.items():
        return_rate_pct = (data["returned"] / data["total"]) * 100.0 if data["total"] else 0.0
        results.append((genre, data["total"], data["returned"], return_rate_pct, data["refund"]))

    return sorted(results, key=lambda x: (x[3], x[4]), reverse=True)


def compute_expected_carrier_otd(source_csv_path: str) -> list[tuple[str, int, int, float]]:
    """Compute on-time delivery rate percentage for delivered orders by shipping carrier."""
    carrier_stats = defaultdict(lambda: {"delivered": 0, "ontime": 0})
    with open(source_csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("fulfillment_status", "").strip().lower() != "delivered":
                continue
            carrier = row.get("carrier_name", "").strip()
            est = row.get("estimated_delivery_date", "").strip()
            act = row.get("actual_delivery_date", "").strip()
            if carrier and est and act:
                carrier_stats[carrier]["delivered"] += 1
                est_d = datetime.strptime(est.split()[0], "%Y-%m-%d").date()
                act_d = datetime.strptime(act.split()[0], "%Y-%m-%d").date()
                if act_d <= est_d:
                    carrier_stats[carrier]["ontime"] += 1

    results = []
    for carrier, data in carrier_stats.items():
        ontime_pct = (data["ontime"] / data["delivered"]) * 100.0 if data["delivered"] else 0.0
        results.append((carrier, data["delivered"], data["ontime"], ontime_pct))

    return sorted(results, key=lambda x: (x[3], x[1]), reverse=True)


def compute_expected_carrier_delivery_time(source_csv_path: str) -> list[tuple[str, int, float]]:
    """Compute average delivery turnaround time in hours for delivered orders per carrier."""
    carrier_hours = defaultdict(list)
    with open(source_csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("fulfillment_status", "").strip().lower() != "delivered":
                continue
            carrier = row.get("carrier_name", "").strip()
            order_ts = row.get("order_timestamp", "").strip()
            act_ts = row.get("actual_delivery_date", "").strip()
            if carrier and order_ts and act_ts:
                o_dt = datetime.strptime(order_ts, "%Y-%m-%d %H:%M:%S")
                d_dt = datetime.strptime(act_ts, "%Y-%m-%d %H:%M:%S")
                hours = (d_dt - o_dt).total_seconds() / 3600.0
                carrier_hours[carrier].append(hours)

    results = []
    for carrier, hours_list in carrier_hours.items():
        avg_hours = sum(hours_list) / len(hours_list) if hours_list else 0.0
        results.append((carrier, len(hours_list), avg_hours))

    return sorted(results, key=lambda x: x[2])


def compute_expected_channel_performance(source_csv_path: str) -> list[tuple[str, int, float, float, float, float]]:
    """Compute order count, gross revenue, refunds, net revenue, and Net AOV per sales channel."""
    channel_stats = defaultdict(lambda: {"orders": 0, "gross": 0.0, "refund": 0.0, "net": 0.0})
    with open(source_csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            channel = row.get("order_channel", "").strip()
            gross = float(row.get("order_grand_total") or 0.0)
            refund = float(row.get("refund_amount") or 0.0)
            net = gross - refund
            channel_stats[channel]["orders"] += 1
            channel_stats[channel]["gross"] += gross
            channel_stats[channel]["refund"] += refund
            channel_stats[channel]["net"] += net

    results = []
    for channel, data in channel_stats.items():
        aov = data["net"] / data["orders"] if data["orders"] else 0.0
        results.append((channel, data["orders"], data["gross"], data["refund"], data["net"], aov))

    return sorted(results, key=lambda x: x[4], reverse=True)


# ==============================================================================
# Helper to resolve source CSV path in workspace or fallback
# ==============================================================================

def _get_source_csv(workspace_dir: str) -> str:
    source_csv = os.path.join(workspace_dir, "bookstore_orders.csv")
    return source_csv if os.path.exists(source_csv) else _csv_source


def _validate_tabular_csv(
    workspace_dir: str,
    filepath: str,
    expected_rows: list,
    col_matchers: list[tuple[str, list[str]]],
    row_validator,
    success_msg: str,
) -> tuple[bool, str]:
    target_path = os.path.join(workspace_dir, filepath)
    if not os.path.exists(target_path):
        available = [f for f in os.listdir(workspace_dir) if not f.startswith(".")]
        return False, f"File '{filepath}' not found in workspace '{workspace_dir}'. Available files: {available}"

    try:
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_rows = [r for r in csv.reader(f) if any(cell.strip() for cell in r)]
    except Exception as e:
        return False, f"Failed to parse CSV '{filepath}': {e}"

    if not raw_rows:
        return False, f"File '{filepath}' is empty."

    data_rows = raw_rows[1:]
    if len(data_rows) != len(expected_rows):
        return False, (
            f"Row count mismatch: expected exactly {len(expected_rows)} data rows, "
            f"but found {len(data_rows)} rows. Header found: {raw_rows[0]}"
        )

    norm_header = [_norm(h) for h in raw_rows[0]]
    indices = []
    for col_name, keywords in col_matchers:
        idx = next((i for i, h in enumerate(norm_header) if any(k in h for k in keywords)), None)
        indices.append(idx if idx is not None else len(indices))

    for rank, (exp, row) in enumerate(zip(expected_rows, data_rows), 1):
        if len(row) <= max(indices):
            req_cols = ", ".join(c[0] for c in col_matchers)
            return False, f"Row {rank} is missing required columns ({req_cols}). Found row: {row}"
        valid, msg = row_validator(rank, exp, row, indices)
        if not valid:
            return False, msg

    return True, success_msg


# ==============================================================================
# CSV Validators with Detailed Error Reporting
# ==============================================================================

def check_top_customers_csv(filepath: str = "top-cust.csv") -> CustomAssert:
    """Validate top-cust.csv against ground truth computed directly from bookstore_orders.csv."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        expected = compute_expected_top_customers(_get_source_csv(workspace_dir), top_n=10)
        def _check_row(rank, exp, row, idxs):
            name_idx, spend_idx = idxs
            exp_name, exp_spend = exp
            act_name = row[name_idx].strip()
            try:
                act_spend = _clean_float(row[spend_idx])
            except ValueError:
                return False, f"Row {rank} has non-numeric spend value '{row[spend_idx]}' in row: {row}"
            if exp_name.lower() != act_name.lower():
                return False, (
                    f"Row {rank} customer mismatch: expected '{exp_name}' (${exp_spend:.2f}), "
                    f"got '{act_name}' (${act_spend:.2f}). "
                    f"(Check: Did you aggregate orders by customer_id rather than customer name string? "
                    f"Distinct customers who share names must be separated)."
                )
            if abs(act_spend - exp_spend) > 0.05:
                return False, (
                    f"Row {rank} ({exp_name}) spend mismatch: expected ${exp_spend:.2f}, "
                    f"got ${act_spend:.2f} (diff: ${abs(act_spend - exp_spend):.2f}). "
                    f"(Check: Did you subtract refund_amount for returns?)"
                )
            return True, ""

        top_preview = f"Rank 1: {expected[0][0]} (${expected[0][1]:.2f}) ... Rank 10: {expected[-1][0]} (${expected[-1][1]:.2f})"
        return _validate_tabular_csv(
            workspace_dir, filepath, expected,
            [("FullName", ["fullname", "name"]), ("TotalSpend", ["spend", "total", "amount"])],
            _check_row, f"All 10 rows match ground truth ({top_preview})."
        )
    return custom_check(_validate)


check_csv = check_top_customers_csv


def check_top_authors_csv(filepath: str = "top-authors.csv") -> CustomAssert:
    """Validate top-authors.csv (Author, UnitsSold, GrossSales) against ground truth."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        expected = compute_expected_top_authors(_get_source_csv(workspace_dir), top_n=10)
        def _check_row(rank, exp, row, idxs):
            auth_idx, units_idx, sales_idx = idxs
            exp_auth, exp_units, exp_sales = exp
            act_auth = row[auth_idx].strip()
            try:
                act_units = int(round(_clean_float(row[units_idx])))
                act_sales = _clean_float(row[sales_idx])
            except ValueError:
                return False, f"Row {rank} contains invalid numeric data in row: {row}"
            if exp_auth.lower() != act_auth.lower():
                return False, (
                    f"Row {rank} author mismatch: expected '{exp_auth}' (Units: {exp_units}, Gross: ${exp_sales:.2f}), "
                    f"got '{act_auth}' (Units: {act_units}, Gross: ${act_sales:.2f}). "
                    f"(Check: Did you aggregate across BOTH primary and secondary items, and sort by GrossSales descending?)"
                )
            if act_units != exp_units:
                return False, (
                    f"Row {rank} ({exp_auth}) units mismatch: expected {exp_units} units, "
                    f"got {act_units} units (diff: {abs(act_units - exp_units)}). "
                    f"(Check: Did you include secondary_item_quantity when secondary items are present?)"
                )
            if abs(act_sales - exp_sales) > 0.05:
                return False, (
                    f"Row {rank} ({exp_auth}) gross sales mismatch: expected ${exp_sales:.2f}, "
                    f"got ${act_sales:.2f} (diff: ${abs(act_sales - exp_sales):.2f})."
                )
            return True, ""

        return _validate_tabular_csv(
            workspace_dir, filepath, expected,
            [("Author", ["author", "name"]), ("UnitsSold", ["unit", "qty", "quantity", "count", "sold"]), ("GrossSales", ["gross", "sale", "revenue", "total", "spend"])],
            _check_row, f"All 10 rows match top authors ground truth (Rank 1: {expected[0][0]} with {expected[0][1]} units, ${expected[0][2]:.2f})."
        )
    return custom_check(_validate)


def check_genre_returns_csv(filepath: str = "genre-returns.csv") -> CustomAssert:
    """Validate genre-returns.csv (Genre, TotalOrders, ReturnedOrders, ReturnRatePct, TotalRefund)."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        expected = compute_expected_genre_returns(_get_source_csv(workspace_dir))
        def _check_row(rank, exp, row, idxs):
            genre_idx, tot_idx, ret_idx, pct_idx, ref_idx = idxs
            exp_genre, exp_tot, exp_ret, exp_pct, exp_ref = exp
            act_genre = row[genre_idx].strip()
            try:
                act_tot = int(round(_clean_float(row[tot_idx])))
                act_ret = int(round(_clean_float(row[ret_idx])))
                act_pct = _clean_float(row[pct_idx])
                act_ref = _clean_float(row[ref_idx])
            except ValueError as e:
                return False, f"Row {rank} contains invalid numeric data: {row} (error: {e})"
            if exp_genre.lower() != act_genre.lower():
                return False, (
                    f"Row {rank} genre mismatch: expected '{exp_genre}' (Total: {exp_tot}, Ret: {exp_ret}, Rate: {exp_pct:.2f}%, Ref: ${exp_ref:.2f}), "
                    f"got '{act_genre}' (Total: {act_tot}, Ret: {act_ret}, Rate: {act_pct:.2f}%, Ref: ${act_ref:.2f}). "
                    f"(Check: Sort by ReturnRatePct descending, with ties broken by TotalRefund descending)."
                )
            if act_tot != exp_tot or act_ret != exp_ret:
                return False, (
                    f"Row {rank} ({exp_genre}) order count mismatch: expected Total={exp_tot}, Returned={exp_ret}, "
                    f"got Total={act_tot}, Returned={act_ret}."
                )
            if abs(act_pct - exp_pct) > 0.1:
                return False, (
                    f"Row {rank} ({exp_genre}) return rate mismatch: expected {exp_pct:.2f}%, "
                    f"got {act_pct:.2f}% (diff: {abs(act_pct - exp_pct):.2f}%)."
                )
            if abs(act_ref - exp_ref) > 0.05:
                return False, (
                    f"Row {rank} ({exp_genre}) refund mismatch: expected ${exp_ref:.2f}, "
                    f"got ${act_ref:.2f} (diff: ${abs(act_ref - exp_ref):.2f})."
                )
            return True, ""

        return _validate_tabular_csv(
            workspace_dir, filepath, expected,
            [("Genre", ["genre", "category"]), ("TotalOrders", ["tot", "order", "count"]), ("ReturnedOrders", ["return", "ret"]), ("ReturnRatePct", ["pct", "rate", "percent"]), ("TotalRefund", ["refund"])],
            _check_row, f"All {len(expected)} genre rows match ground truth."
        )
    return custom_check(_validate)


def check_carrier_otd_csv(filepath: str = "carrier-otd.csv") -> CustomAssert:
    """Validate carrier-otd.csv (CarrierName, TotalDelivered, OnTimeDelivered, OnTimePct)."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        expected = compute_expected_carrier_otd(_get_source_csv(workspace_dir))
        def _check_row(rank, exp, row, idxs):
            carrier_idx, deliv_idx, ontime_idx, pct_idx = idxs
            exp_carrier, exp_deliv, exp_ontime, exp_pct = exp
            act_carrier = row[carrier_idx].strip()
            try:
                act_deliv = int(round(_clean_float(row[deliv_idx])))
                act_ontime = int(round(_clean_float(row[ontime_idx])))
                act_pct = _clean_float(row[pct_idx])
            except ValueError as e:
                return False, f"Row {rank} contains invalid numeric data: {row} (error: {e})"
            if exp_carrier.lower() != act_carrier.lower():
                return False, (
                    f"Row {rank} carrier mismatch: expected '{exp_carrier}' (Delivered: {exp_deliv}, OnTime: {exp_ontime}, OTD: {exp_pct:.2f}%), "
                    f"got '{act_carrier}' (Delivered: {act_deliv}, OnTime: {act_ontime}, OTD: {act_pct:.2f}%). "
                    f"(Check: Did you compare calendar dates 'YYYY-MM-DD' rather than datetime timestamps? "
                    f"estimated_delivery_date is a date without time, so comparing full timestamps treats deliveries on the estimated date as late)."
                )
            if act_deliv != exp_deliv or act_ontime != exp_ontime:
                return False, (
                    f"Row {rank} ({exp_carrier}) count mismatch: expected Delivered={exp_deliv}, OnTime={exp_ontime}, "
                    f"got Delivered={act_deliv}, OnTime={act_ontime}. "
                    f"(Check: An order is on-time if actual_delivery_date.date() <= estimated_delivery_date.date())."
                )
            if abs(act_pct - exp_pct) > 0.1:
                return False, (
                    f"Row {rank} ({exp_carrier}) OTD % mismatch: expected {exp_pct:.2f}%, "
                    f"got {act_pct:.2f}% (diff: {abs(act_pct - exp_pct):.2f}%)."
                )
            return True, ""

        return _validate_tabular_csv(
            workspace_dir, filepath, expected,
            [("CarrierName", ["carrier", "name"]), ("TotalDelivered", ["deliv", "total", "count", "order"]), ("OnTimeDelivered", ["ontime"]), ("OnTimePct", ["pct", "rate", "percent"])],
            _check_row, f"All {len(expected)} carriers match ground truth OTD metrics."
        )
    return custom_check(_validate)


def check_carrier_delivery_time_csv(filepath: str = "carrier-delivery-time.csv") -> CustomAssert:
    """Validate carrier-delivery-time.csv (CarrierName, DeliveredOrders, AvgDeliveryHours)."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        expected = compute_expected_carrier_delivery_time(_get_source_csv(workspace_dir))
        def _check_row(rank, exp, row, idxs):
            carrier_idx, count_idx, hours_idx = idxs
            exp_carrier, exp_count, exp_hours = exp
            act_carrier = row[carrier_idx].strip()
            try:
                act_count = int(round(_clean_float(row[count_idx])))
                act_hours = _clean_float(row[hours_idx])
            except ValueError as e:
                return False, f"Row {rank} contains invalid numeric data: {row} (error: {e})"
            if exp_carrier.lower() != act_carrier.lower():
                return False, (
                    f"Row {rank} carrier mismatch: expected '{exp_carrier}' (Delivered: {exp_count}, AvgHours: {exp_hours:.2f}), "
                    f"got '{act_carrier}' (Delivered: {act_count}, AvgHours: {act_hours:.2f}). "
                    f"(Check: Sort by AvgDeliveryHours ascending so fastest carriers appear first)."
                )
            if act_count != exp_count:
                return False, (
                    f"Row {rank} ({exp_carrier}) delivered count mismatch: expected {exp_count} delivered orders, "
                    f"got {act_count}."
                )
            if abs(act_hours - exp_hours) > 0.1:
                return False, (
                    f"Row {rank} ({exp_carrier}) average hours mismatch: expected {exp_hours:.2f} hrs, "
                    f"got {act_hours:.2f} hrs (diff: {abs(act_hours - exp_hours):.2f} hrs)."
                )
            return True, ""

        return _validate_tabular_csv(
            workspace_dir, filepath, expected,
            [("CarrierName", ["carrier", "name"]), ("DeliveredOrders", ["deliv", "order", "count", "total", "num"]), ("AvgDeliveryHours", ["hour", "time", "avg"])],
            _check_row, f"All {len(expected)} carriers match delivery time ground truth."
        )
    return custom_check(_validate)


def check_channel_performance_csv(filepath: str = "channel-performance.csv") -> CustomAssert:
    """Validate channel-performance.csv (Channel, OrderCount, GrossRevenue, TotalRefunds, NetRevenue, NetAOV)."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        expected = compute_expected_channel_performance(_get_source_csv(workspace_dir))
        def _check_row(rank, exp, row, idxs):
            ch_idx, count_idx, gross_idx, ref_idx, net_idx, aov_idx = idxs
            exp_ch, exp_count, exp_gross, exp_ref, exp_net, exp_aov = exp
            act_ch = row[ch_idx].strip()
            try:
                act_count = int(round(_clean_float(row[count_idx])))
                act_gross = _clean_float(row[gross_idx])
                act_ref = _clean_float(row[ref_idx])
                act_net = _clean_float(row[net_idx])
                act_aov = _clean_float(row[aov_idx])
            except ValueError as e:
                return False, f"Row {rank} contains invalid numeric data: {row} (error: {e})"
            if exp_ch.lower() != act_ch.lower():
                return False, (
                    f"Row {rank} channel mismatch: expected '{exp_ch}' (Orders: {exp_count}, Gross: ${exp_gross:.2f}, Ref: ${exp_ref:.2f}, Net: ${exp_net:.2f}, NetAOV: ${exp_aov:.2f}), "
                    f"got '{act_ch}' (Orders: {act_count}, Gross: ${act_gross:.2f}, Ref: ${act_ref:.2f}, Net: ${act_net:.2f}, NetAOV: ${act_aov:.2f}). "
                    f"(Check: Sort by NetRevenue descending)."
                )
            if act_count != exp_count:
                return False, f"Row {rank} ({exp_ch}) order count mismatch: expected {exp_count} orders, got {act_count}."
            if abs(act_gross - exp_gross) > 0.05:
                return False, f"Row {rank} ({exp_ch}) gross revenue mismatch: expected ${exp_gross:.2f}, got ${act_gross:.2f} (diff: ${abs(act_gross - exp_gross):.2f})."
            if abs(act_ref - exp_ref) > 0.05:
                return False, f"Row {rank} ({exp_ch}) refund mismatch: expected ${exp_ref:.2f}, got ${act_ref:.2f} (diff: ${abs(act_ref - exp_ref):.2f})."
            if abs(act_net - exp_net) > 0.05:
                return False, f"Row {rank} ({exp_ch}) net revenue mismatch: expected ${exp_net:.2f}, got ${act_net:.2f} (diff: ${abs(act_net - exp_net):.2f})."
            if abs(act_aov - exp_aov) > 0.05:
                return False, f"Row {rank} ({exp_ch}) Net AOV mismatch: expected ${exp_aov:.2f}, got ${act_aov:.2f} (diff: ${abs(act_aov - exp_aov):.2f})."
            return True, ""

        return _validate_tabular_csv(
            workspace_dir, filepath, expected,
            [("Channel", ["channel", "name"]), ("OrderCount", ["count", "order", "num"]), ("GrossRevenue", ["gross"]), ("TotalRefunds", ["refund", "return"]), ("NetRevenue", ["net", "profit"]), ("NetAOV", ["aov", "average"])],
            _check_row, f"All {len(expected)} channels match financial performance ground truth."
        )
    return custom_check(_validate)


# ==============================================================================
# Multi-Step Evaluation Test
# ==============================================================================

TEST = Test(
    name="data_analytics",
    description="Multi-step data analytics and business intelligence evaluation on bookstore orders dataset",
    setup=[
        f"cp {_csv_source} .",
    ],
    steps=[
        Step(
            prompt="""Use Python to find the customers (by ID) who spent the most amount at the store, making sure to subtract any returns they made. Produce a "top-cust.csv" with 2 columns - FullName and TotalSpend. Only record the top 10 people.""",
            checks=[
                git_changes("top-cust.csv", "A", total_lines=(10, 12)),
                check_top_customers_csv(),
            ],
            point=2,
        ),
        Step(
            prompt="""Use Python to find the top authors by gross sales across all orders in bookstore_orders.csv. Make sure to account for both primary and secondary items. Produce a "top-authors.csv" with 3 columns - Author, UnitsSold, and GrossSales. Record the top 10 authors sorted by GrossSales in descending order.""",
            checks=[
                git_changes("top-authors.csv", "A", total_lines=(10, 12)),
                check_top_authors_csv(),
            ],
            point=2,
        ),
        Step(
            prompt="""Use Python to analyze product returns by book genre from the source csv. Group by primary item genre and calculate: TotalOrders, ReturnedOrders, ReturnRatePct (rounded to 2 decimals), and TotalRefund (sum of refund_amount rounded to 2 decimal places). Produce a "genre-returns.csv" with columns: Genre, TotalOrders, ReturnedOrders, ReturnRatePct, TotalRefund. Sort by ReturnRatePct descending, breaking ties with TotalRefund descending.""",
            checks=[
                git_changes("genre-returns.csv", "A", total_lines=(18, 20)),
                check_genre_returns_csv(),
            ],
            point=2,
        ),
        Step(
            prompt="""Calculate the On-Time Delivery performance for each shipping carrier. Filter for orders that have been successfully delivered. Group by carrier_name and calculate: TotalDelivered (number of delivered orders), OnTimeDelivered (number of on-time orders), and OnTimePct (percentage of delivered orders on-time, e.g. 93.33). Produce a "carrier-otd.csv" with columns: CarrierName, TotalDelivered, OnTimeDelivered, OnTimePct. Sort by OnTimePct descending.""",
            checks=[
                git_changes("carrier-otd.csv", "A", total_lines=(5, 7)),
                check_carrier_otd_csv(),
            ],
            point=2,
        ),
        Step(
            prompt="""Use Python to calculate the average delivery turnaround time in hours for each carrier on delivered orders (fulfillment_status is "Delivered"). For each order, compute the turnaround time in hours as the difference between order_timestamp and actual_delivery_date. Group by carrier_name and calculate: DeliveredOrders (count of delivered orders) and AvgDeliveryHours (average delivery time in hours rounded to 2 decimal places). Produce a "carrier-delivery-time.csv" with columns: CarrierName, DeliveredOrders, AvgDeliveryHours. Sort by AvgDeliveryHours ascending (fastest average delivery time first).""",
            checks=[
                git_changes("carrier-delivery-time.csv", "A", total_lines=(5, 7)),
                check_carrier_delivery_time_csv(),
            ],
            point=2,
        ),
        Step(
            prompt="""Use Python to evaluate channel performance across all sales channels in bookstore_orders.csv. Group by order_channel and calculate: OrderCount (number of orders), GrossRevenue (sum of order_grand_total), TotalRefunds (sum of refund_amount), NetRevenue (GrossRevenue minus TotalRefunds), and NetAOV (NetRevenue divided by OrderCount, rounded to 2 decimal places). Produce a "channel-performance.csv" with columns: Channel, OrderCount, GrossRevenue, TotalRefunds, NetRevenue, NetAOV. Sort by NetRevenue descending.""",
            checks=[
                git_changes("channel-performance.csv", "A", total_lines=(7, 9)),
                check_channel_performance_csv(),
            ],
            point=2,
        ),
    ],
)
