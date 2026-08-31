"""
Tests for restock order API endpoints.
"""
import re
from datetime import datetime, timedelta

import pytest

from mock_data import restock_orders


@pytest.fixture(autouse=True)
def reset_restock_orders():
    """Restock orders live in a module-level list; clear it so tests are independent.

    The list must be cleared in place (never reassigned) because main.py holds a
    reference to the same list object.
    """
    restock_orders.clear()
    yield
    restock_orders.clear()


def _post_order(client, budget, items):
    """Helper to submit a restock order payload."""
    return client.post("/api/restock-orders", json={"budget": budget, "items": items})


class TestRestockOrdersEndpoints:
    """Test suite for restock-order endpoints."""

    def test_get_restock_orders_empty(self, client):
        """A fresh server session has no restock orders."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restock_order_success(self, client):
        """Happy path: a single-line order is created with computed fields."""
        response = _post_order(client, 10000, [{"sku": "TMP-201", "quantity": 10}])
        assert response.status_code == 201

        order = response.json()
        for field in [
            "id", "order_number", "status", "order_date", "expected_delivery",
            "lead_time_days", "budget", "total_value", "items",
        ]:
            assert field in order

        assert order["status"] == "Submitted"
        assert re.fullmatch(r"RST-\d{4}-\d{4}", order["order_number"])
        assert order["budget"] == 10000
        # TMP-201 unit_cost is 89.5
        assert abs(order["total_value"] - 895.0) < 0.01
        # order_date must use the same "YYYY-MM-DDTHH:MM:SS" shape as orders.json
        assert "T" in order["order_date"]
        assert len(order["order_date"]) == 19
        assert order["expected_delivery"] > order["order_date"]

        assert len(order["items"]) == 1
        item = order["items"][0]
        assert item["sku"] == "TMP-201"
        assert item["quantity"] == 10
        assert item["category"] == "Sensors"
        assert item["warehouse"] == "London"
        assert abs(item["unit_price"] - 89.5) < 0.01
        assert abs(item["line_total"] - 895.0) < 0.01

    @pytest.mark.parametrize(
        "sku,expected_lead_days",
        [
            ("PCB-001", 14),  # Circuit Boards
            ("TMP-201", 10),  # Sensors
            ("SRV-301", 21),  # Actuators
            ("MCU-401", 18),  # Controllers
            ("PSU-501", 12),  # Power Supplies
        ],
    )
    def test_create_restock_order_lead_time_by_category(self, client, sku, expected_lead_days):
        """Each category maps to its fixed lead time and the delivery date reflects it."""
        response = _post_order(client, 100000, [{"sku": sku, "quantity": 1}])
        assert response.status_code == 201

        order = response.json()
        item = order["items"][0]
        assert item["lead_time_days"] == expected_lead_days
        assert order["lead_time_days"] == expected_lead_days

        order_date = datetime.fromisoformat(order["order_date"])
        expected_delivery = datetime.fromisoformat(item["expected_delivery"])
        assert expected_delivery - order_date == timedelta(days=expected_lead_days)

    def test_create_restock_order_multi_line_uses_max_lead_time(self, client):
        """Order-level lead time is the slowest line's lead time."""
        response = _post_order(
            client,
            100000,
            [{"sku": "TMP-201", "quantity": 1}, {"sku": "SRV-301", "quantity": 1}],
        )
        assert response.status_code == 201

        order = response.json()
        assert order["lead_time_days"] == 21
        latest_line_delivery = max(item["expected_delivery"] for item in order["items"])
        assert order["expected_delivery"] == latest_line_delivery

    def test_create_restock_order_empty_items(self, client):
        """An order with no lines is rejected."""
        response = _post_order(client, 1000, [])
        assert response.status_code in (400, 422)
        assert "detail" in response.json()

    def test_create_restock_order_zero_quantity(self, client):
        """Zero quantity fails Pydantic validation."""
        response = _post_order(client, 1000, [{"sku": "TMP-201", "quantity": 0}])
        assert response.status_code == 422

    def test_create_restock_order_negative_quantity(self, client):
        """Negative quantity fails Pydantic validation."""
        response = _post_order(client, 1000, [{"sku": "TMP-201", "quantity": -5}])
        assert response.status_code == 422

    def test_create_restock_order_negative_budget(self, client):
        """Negative budget fails Pydantic validation."""
        response = _post_order(client, -1, [{"sku": "TMP-201", "quantity": 1}])
        assert response.status_code == 422

    def test_create_restock_order_over_budget(self, client):
        """An order whose total exceeds the budget is rejected."""
        # SRV-302 unit_cost is 725, budget is 100
        response = _post_order(client, 100, [{"sku": "SRV-302", "quantity": 1}])
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()

    def test_create_restock_order_exact_budget_accepted(self, client):
        """An order that exactly matches the budget is accepted."""
        # PSU-501 unit_cost is 18.99; 10 units = 189.90
        response = _post_order(client, 189.90, [{"sku": "PSU-501", "quantity": 10}])
        assert response.status_code == 201

    def test_create_restock_order_unknown_sku(self, client):
        """An unknown SKU is rejected."""
        response = _post_order(client, 1000, [{"sku": "NOPE-999", "quantity": 1}])
        assert response.status_code == 400
        assert "unknown sku" in response.json()["detail"].lower()

    def test_create_then_get_restock_order(self, client):
        """A created order is returned by the list endpoint."""
        created = _post_order(client, 10000, [{"sku": "TMP-201", "quantity": 2}]).json()

        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == created["id"]
        assert data[0]["order_number"] == created["order_number"]

    def test_restock_order_numbers_increment(self, client):
        """Consecutive orders get consecutive sequence numbers."""
        first = _post_order(client, 10000, [{"sku": "TMP-201", "quantity": 1}]).json()
        second = _post_order(client, 10000, [{"sku": "PSU-501", "quantity": 1}]).json()
        assert first["order_number"].endswith("-0001")
        assert second["order_number"].endswith("-0002")
        assert first["id"] != second["id"]

    def test_restock_order_total_value_calculation(self, client):
        """total_value equals the sum of line totals."""
        response = _post_order(
            client,
            100000,
            [
                {"sku": "TMP-201", "quantity": 3},
                {"sku": "PSU-508", "quantity": 2},
                {"sku": "ACC-206", "quantity": 5},
            ],
        )
        assert response.status_code == 201
        order = response.json()
        line_sum = sum(item["line_total"] for item in order["items"])
        assert abs(order["total_value"] - line_sum) < 0.01
        # 3*89.5 + 2*185.5 + 5*156 = 268.5 + 371 + 780
        assert abs(order["total_value"] - 1419.5) < 0.01
