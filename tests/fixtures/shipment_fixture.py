"""Deterministic benchmark shipment pricing fixture for test_specification_synthesis_gate.py."""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import Any

from runtime.llm_adapter import DeterministicRuleBasedAdapter

SHIPMENT_PRICING_SOURCE = '''"""Shipment pricing calculator for domestic fulfillment zones."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

_ZONE_SURCHARGES: dict[str, int] = {
    "urban": 0,
    "suburban": 10000,
    "rural": 25000,
}


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def quote_shipment(
    weight_kg: float,
    distance_km: float,
    zone: str,
    fragile: bool = False,
) -> dict[str, int | str]:
    """Calculate a deterministic VND shipment quote."""
    if weight_kg < 0:
        raise ValueError("weight_kg cannot be negative")
    if distance_km < 0:
        raise ValueError("distance_km cannot be negative")
    if not isinstance(zone, str) or zone not in _ZONE_SURCHARGES:
        raise ValueError(f"Unknown shipping zone: {zone!r}")

    started_kg = int(math.ceil(float(weight_kg)))
    started_km = int(math.ceil(float(distance_km)))

    base_fee = 20000 + (6000 * started_kg)
    distance_fee = 1500 * started_km
    zone_surcharge = _ZONE_SURCHARGES[zone]

    subtotal = base_fee + distance_fee + zone_surcharge
    fragile_fee = _round_half_up(Decimal(subtotal) * Decimal("0.10")) if fragile else 0
    surcharge = zone_surcharge + fragile_fee
    total = base_fee + distance_fee + surcharge

    return {
        "base_fee": int(base_fee),
        "distance_fee": int(distance_fee),
        "surcharge": int(surcharge),
        "total": int(total),
        "currency": "VND",
    }
'''

TEST_SHIPMENT_PRICING_SOURCE = '''"""Unit tests for quote_shipment in shipment_pricing.py (11 independent tests)."""
from __future__ import annotations

import pytest

from shipment_pricing import quote_shipment


def test_zero_weight_and_zero_distance() -> None:
    res = quote_shipment(0.0, 0.0, "urban", fragile=False)
    assert res == {
        "base_fee": 20000,
        "distance_fee": 0,
        "surcharge": 0,
        "total": 20000,
        "currency": "VND",
    }


def test_ceil_boundary_weight() -> None:
    res_frac = quote_shipment(1.01, 2.0, "urban")
    assert res_frac["base_fee"] == 20000 + (2 * 6000)
    res_exact = quote_shipment(1.0, 2.0, "urban")
    assert res_exact["base_fee"] == 20000 + (1 * 6000)


def test_ceil_boundary_distance() -> None:
    res_frac = quote_shipment(1.0, 3.2, "urban")
    assert res_frac["distance_fee"] == 4 * 1500
    res_exact = quote_shipment(1.0, 3.0, "urban")
    assert res_exact["distance_fee"] == 3 * 1500


def test_zone_urban() -> None:
    res = quote_shipment(2.0, 10.0, "urban")
    assert res["surcharge"] == 0
    assert res["total"] == 32000 + 15000


def test_zone_suburban() -> None:
    res = quote_shipment(2.0, 10.0, "suburban")
    assert res["surcharge"] == 10000
    assert res["total"] == 32000 + 15000 + 10000


def test_zone_rural() -> None:
    res = quote_shipment(2.0, 10.0, "rural")
    assert res["surcharge"] == 25000
    assert res["total"] == 32000 + 15000 + 25000


def test_fragile_surcharge_urban() -> None:
    res = quote_shipment(2.0, 10.0, "urban", fragile=True)
    # subtotal = 32000 + 15000 + 0 = 47000; fragile = 4700
    assert res["surcharge"] == 4700
    assert res["total"] == 51700


def test_fragile_surcharge_rural() -> None:
    res = quote_shipment(1.0, 1.0, "rural", fragile=True)
    # base=26000, dist=1500, zone=25000 -> subtotal=52500 -> fragile=5250 -> surcharge=30250
    assert res["surcharge"] == 30250
    assert res["total"] == 57750


def test_invalid_negative_weight() -> None:
    with pytest.raises(ValueError):
        quote_shipment(-0.1, 5.0, "urban")


def test_invalid_negative_distance() -> None:
    with pytest.raises(ValueError):
        quote_shipment(1.0, -2.0, "suburban")


def test_unknown_zone_and_return_types_keys() -> None:
    with pytest.raises(ValueError):
        quote_shipment(1.0, 5.0, "mars_colony")
    res = quote_shipment(1.5, 4.5, "suburban", fragile=True)
    assert set(res.keys()) == {"base_fee", "distance_fee", "surcharge", "total", "currency"}
    for k in ("base_fee", "distance_fee", "surcharge", "total"):
        assert isinstance(res[k], int)
    assert res["currency"] == "VND"
'''

_BASELINE_BENCHMARK_FILES: dict[str, str] = {
    "fulfillment_models.py": '''"""Domain models for benchmark fulfillment workspace."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderItem:
    sku: str
    quantity: int
    unit_price: int
''',
    "inventory_ledger.py": '''"""Inventory reservation helpers for benchmark fulfillment workspace."""
from __future__ import annotations


def reserve_stock(stock: dict[str, int], sku: str, qty: int) -> dict[str, int]:
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    available = stock.get(sku, 0)
    if available < qty:
        raise ValueError(f"Insufficient stock for {sku}")
    updated = dict(stock)
    updated[sku] = available - qty
    return updated
''',
    "order_pipeline.py": '''"""Order subtotal and discount pipeline for benchmark fulfillment workspace."""
from __future__ import annotations

from fulfillment_models import OrderItem
from inventory_ledger import reserve_stock


def compute_order_subtotal(items: list[OrderItem]) -> int:
    return sum(item.quantity * item.unit_price for item in items)


def apply_loyalty_discount(subtotal: int, tier: str) -> int:
    rates = {"bronze": 0, "silver": 5, "gold": 10}
    if tier not in rates:
        raise ValueError(f"Unknown loyalty tier: {tier}")
    return subtotal - ((subtotal * rates[tier]) // 100)


def process_order(stock: dict[str, int], items: list[OrderItem], tier: str = "bronze") -> tuple[dict[str, int], int]:
    cur_stock = dict(stock)
    for item in items:
        cur_stock = reserve_stock(cur_stock, item.sku, item.quantity)
    subtotal = compute_order_subtotal(items)
    return cur_stock, apply_loyalty_discount(subtotal, tier)
''',
    "test_fulfillment_pipeline.py": '''"""Baseline 10 unit tests for the 4-file benchmark workspace."""
from __future__ import annotations

import pytest

from fulfillment_models import OrderItem
from inventory_ledger import reserve_stock
from order_pipeline import apply_loyalty_discount, compute_order_subtotal, process_order


def test_01_order_item_fields() -> None:
    item = OrderItem(sku="SKU-1", quantity=2, unit_price=15000)
    assert item.sku == "SKU-1" and item.quantity == 2 and item.unit_price == 15000


def test_02_reserve_stock_success() -> None:
    updated = reserve_stock({"SKU-1": 10}, "SKU-1", 4)
    assert updated["SKU-1"] == 6


def test_03_reserve_stock_insufficient() -> None:
    with pytest.raises(ValueError):
        reserve_stock({"SKU-1": 2}, "SKU-1", 5)


def test_04_reserve_stock_nonpositive() -> None:
    with pytest.raises(ValueError):
        reserve_stock({"SKU-1": 10}, "SKU-1", 0)


def test_05_compute_order_subtotal() -> None:
    items = [OrderItem("A", 2, 10000), OrderItem("B", 1, 5000)]
    assert compute_order_subtotal(items) == 25000


def test_06_discount_bronze() -> None:
    assert apply_loyalty_discount(100000, "bronze") == 100000


def test_07_discount_silver() -> None:
    assert apply_loyalty_discount(100000, "silver") == 95000


def test_08_discount_gold() -> None:
    assert apply_loyalty_discount(100000, "gold") == 90000


def test_09_discount_unknown_tier() -> None:
    with pytest.raises(ValueError):
        apply_loyalty_discount(100000, "platinum")


def test_10_process_order_end_to_end() -> None:
    stock, final_total = process_order({"A": 5, "B": 3}, [OrderItem("A", 2, 50000)], tier="gold")
    assert stock["A"] == 3
    assert final_total == 90000
''',
}

_TEMP_BENCHMARK_DIR: Path | None = None


def get_codex_like_eval_workspace(repo_root: Path | None = None) -> Path:
    """Return existing benchmarks/codex_like_eval or materialize a disposable 4-file benchmark template."""
    global _TEMP_BENCHMARK_DIR
    if repo_root is not None:
        candidate = repo_root / "benchmarks" / "codex_like_eval"
        if candidate.is_dir() and (candidate / "order_pipeline.py").is_file():
            return candidate
    if _TEMP_BENCHMARK_DIR is None or not _TEMP_BENCHMARK_DIR.is_dir():
        td = Path(tempfile.mkdtemp(prefix="codex_like_eval_fixture_"))
        for fname, content in _BASELINE_BENCHMARK_FILES.items():
            (td / fname).write_text(content, encoding="utf-8")
        _TEMP_BENCHMARK_DIR = td
        atexit.register(lambda p=td: shutil.rmtree(p, ignore_errors=True))
    return _TEMP_BENCHMARK_DIR


class BenchmarkShipmentPricingAdapter(DeterministicRuleBasedAdapter):
    """Test fixture adapter for deterministic shipment pricing feature synthesis and repair."""

    def _synthesize_feature_code_deterministic(
        self,
        objective: str,
        workspace_root: str,
        reqs: dict[str, Any],
    ) -> list[dict[str, str]] | None:
        req_files = reqs.get("required_files_to_create", [])
        req_funcs = reqs.get("required_functions", [])
        if "shipment_pricing.py" not in req_files and "quote_shipment" not in req_funcs:
            return None
        ws = Path(workspace_root) if workspace_root else Path(".")
        return [
            {
                "path": str(ws / "shipment_pricing.py"),
                "content": SHIPMENT_PRICING_SOURCE,
                "description": "Write shipment_pricing.py implementing quote_shipment",
            },
            {
                "path": str(ws / "test_shipment_pricing.py"),
                "content": TEST_SHIPMENT_PRICING_SOURCE,
                "description": "Write test_shipment_pricing.py with 11 unit tests",
            },
        ]

    def _synthesize_feature_from_objective(
        self,
        objective: str,
        workspace_root: str,
        reqs: dict[str, Any],
    ) -> list[dict[str, str]] | None:
        return self._synthesize_feature_code_deterministic(objective, workspace_root, reqs)

    def diagnose_and_repair(
        self,
        objective: str,
        failed_step: dict[str, Any],
        error_context: str,
        workspace_root: str,
    ) -> dict[str, Any] | None:
        if "shipment_pricing.py" in error_context or "quote_shipment" in error_context:
            target_path = str(Path(workspace_root) / "shipment_pricing.py")
            return self._propose_repair(
                target_path,
                SHIPMENT_PRICING_SOURCE,
                "Repaired quote_shipment implementation in shipment_pricing.py",
                workspace_root,
            )
        return super().diagnose_and_repair(objective, failed_step, error_context, workspace_root)
