from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def available_seat(client, event_id=2):
    response = client.get(f"/events/{event_id}/seats")
    assert response.status_code == 200

    seats = response.json()["available"]
    assert seats, f"No available seats for event {event_id}"

    return seats[-1]


def test_health_checks_postgres_and_redis(client):
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["postgres"] is True
    assert data["redis"] is True


def test_hold_conflict_and_release(client):
    event_id = 2
    seat = available_seat(client, event_id)
    holder = f"ci-holder-{uuid4().hex}"
    other_holder = f"ci-other-{uuid4().hex}"

    response = client.post(
        f"/events/{event_id}/hold",
        json={"holder": holder, "seats": [seat]},
    )
    assert response.status_code == 201
    assert seat in response.json()["seats"]

    try:
        conflict = client.post(
            f"/events/{event_id}/hold",
            json={"holder": other_holder, "seats": [seat]},
        )
        assert conflict.status_code == 409
    finally:
        released = client.delete(
            f"/events/{event_id}/holds/{holder}"
        )
        assert released.status_code == 200
        assert seat in released.json()["released"]


def test_confirm_booking_and_prevent_resale(client):
    event_id = 2
    seat = available_seat(client, event_id)
    holder = f"ci-buyer-{uuid4().hex}"
    another_holder = f"ci-resale-{uuid4().hex}"

    held = client.post(
        f"/events/{event_id}/hold",
        json={"holder": holder, "seats": [seat]},
    )
    assert held.status_code == 201

    confirmed = client.post(
        f"/events/{event_id}/confirm",
        json={"holder": holder},
    )
    assert confirmed.status_code == 200

    booking_data = confirmed.json()
    assert booking_data["status"] == "confirmed"
    assert seat in booking_data["seats"]

    ref = booking_data["ref"]
    retrieved = client.get(f"/bookings/{ref}")
    assert retrieved.status_code == 200
    assert retrieved.json()["ref"] == ref
    assert seat in retrieved.json()["seats"]

    seat_map = client.get(f"/events/{event_id}/seats").json()
    assert seat in seat_map["sold"]
    assert seat not in seat_map["available"]

    resale = client.post(
        f"/events/{event_id}/hold",
        json={"holder": another_holder, "seats": [seat]},
    )
    assert resale.status_code == 409
