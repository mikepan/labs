import csv
import os
import re
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from tests.framework import *

from collections import defaultdict

_csv_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookstore_orders.csv")


def compute_expected_top_customers(source_csv_path: str, top_n: int = 10) -> list[tuple[str, float]]:
    """Parse raw bookstore orders CSV using Python to compute the known-good top customers by net spend,
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



def check_csv(filepath: str = "top-cust.csv") -> CustomAssert:
    """Validate the LLM's generated top-cust.csv against ground truth computed directly from bookstore_orders.csv."""

    def _validate(workspace_dir: str) -> tuple[bool, str]:
        target_path = os.path.join(workspace_dir, filepath)
        if not os.path.exists(target_path):
            return False, f"File '{filepath}' not found in workspace."

        # 1. Compute ground-truth known-good results from raw CSV
        source_csv = os.path.join(workspace_dir, "bookstore_orders.csv")
        if not os.path.exists(source_csv):
            source_csv = _csv_source

        expected_top10 = compute_expected_top_customers(source_csv, top_n=10)

        # 2. Parse agent's output CSV
        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                raw_rows = [r for r in reader if any(cell.strip() for cell in r)]
        except Exception as e:
            return False, f"Failed to parse CSV '{filepath}': {e}"

        if not raw_rows:
            return False, f"File '{filepath}' is empty."

        header = raw_rows[0]
        data_rows = raw_rows[1:]

        # 3. Check exact row count (10 rows for top 10 people)
        if len(data_rows) != len(expected_top10):
            return False, f"Expected exactly {len(expected_top10)} data rows, but found {len(data_rows)}."

        # 4. Map columns (Fullname, TotalSpend)
        def norm(s: str) -> str:
            return re.sub(r"[_\s\-]+", "", s.strip().lower())

        norm_header = [norm(h) for h in header]
        name_idx, spend_idx = None, None
        for idx, h in enumerate(norm_header):
            if "fullname" in h or h in "fullname" or "name" in h:
                name_idx = idx
            elif "spend" in h or "total" in h or "amount" in h:
                spend_idx = idx

        if name_idx is None:
            name_idx = 0
        if spend_idx is None:
            spend_idx = 1 if len(header) > 1 else 0

        # 5. Validate each row against the known-good ground truth
        for rank, ((exp_name, exp_spend), row) in enumerate(zip(expected_top10, data_rows), 1):
            if len(row) <= max(name_idx, spend_idx):
                return False, f"Row {rank} is missing required columns: {row}"

            act_name = row[name_idx].strip()
            raw_spend = row[spend_idx].strip()
            clean_spend = re.sub(r"[^\d\.\-]", "", raw_spend)
            try:
                act_spend = float(clean_spend)
            except ValueError:
                return False, f"Row {rank} has non-numeric spend '{raw_spend}'."

            # Verify customer name
            if exp_name.lower() != act_name.lower():
                return False, f"Row {rank} customer mismatch: expected '{exp_name}', got '{act_name}'."

            # Verify spend amount within tolerance
            if abs(act_spend - exp_spend) > 0.05:
                return False, f"Row {rank} ({exp_name}) spend mismatch: expected ${exp_spend:.2f}, got ${act_spend:.2f}."

        top_preview = f"Rank 1: {expected_top10[0][0]} (${expected_top10[0][1]:.2f}) ... Rank 10: {expected_top10[-1][0]} (${expected_top10[-1][1]:.2f})"
        return True, f"All 10 rows perfectly match ground-truth calculation ({top_preview})."

    return custom_check(_validate)



TEST = Test(
    name="data_analytics",
    description="Analyze bookstore orders CSV dataset with Python to find top spending customers subtracting returns",
    setup=[
        f"cp {_csv_source} .",
    ],
    steps=[
        Step(
            prompt="""Use Python to find the customers (by ID) who spent the most amount at the store, making sure to subtract any returns they made. Produce a top-cust.csv with 2 columns - FullName and TotalSpend. Only record the top 10 people.""",
            checks=[
                git_changes("top-cust.csv", "A", total_lines=(10, 12)),
                check_csv(),
            ],
            point=1,
        ),
    ],
)


