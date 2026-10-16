import os
import time
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import cache, db
from .seats import (
    ROWS,
    SeatError,
    find_block,
    parse_seat,
    quote,
    seat_label,
    seconds_left,
)

app = FastAPI(title="event-ticket-booking")

# Overridable so expiry can be tested in seconds instead of ten minutes.
HOLD_SECONDS = int(os.environ.get("HOLD_SECONDS", "600"))

# Check every seat still belongs to this holder, then delete the lot, with no
# window in between. Redis runs this whole script as one operation.
CONFIRM_LUA = """
for i, k in ipairs(KEYS) do
  if redis.call('GET', k) ~= ARGV[1] then return 0 end
end
for i, k in ipairs(KEYS) do redis.call('DEL', k) end
return 1
"""


@app.get("/health")
def health():
    out = {"status": "ok", "postgres": False, "redis": False}

    try:
        db.query("SELECT 1")
        out["postgres"] = True
    except Exception as e:
        out["pg_error"] = str(e)

    try:
        cache.client().ping()
        out["redis"] = True
    except Exception as e:
        out["redis_error"] = str(e)

    if out["postgres"] and out["redis"]:
        return out

    return JSONResponse(out, status_code=503)


def _hold_key(event_id, seat):
    return f"hold:{event_id}:{seat}"


def _all_seats(event):
    rows = ROWS[:event["row_count"]]
    return [
        seat_label(r, n)
        for r in rows
        for n in range(1, event["seats_per_row"] + 1)
    ]


def _event(event_id):
    row = db.one(
        "SELECT id, name, venue, starts_at, base_price, row_count,"
        " seats_per_row FROM events WHERE id=%s",
        (event_id,),
    )

    if not row:
        raise HTTPException(404, "no such event")

    return row


def _sold(event_id):
    return {
        r["seat"]
        for r in db.query(
            "SELECT seat FROM bookings WHERE event_id=%s",
            (event_id,),
        )
    }


def _held(event_id):
    """Seats with a live hold key, as {seat: holder}. Keys that have expired
    simply are not there - that is the whole point of using a TTL."""
    c = cache.client()
    prefix = f"hold:{event_id}:"
    out = {}

    for key in c.scan_iter(f"{prefix}*"):
        holder = c.get(key)
        if holder:
            out[key[len(prefix):]] = holder

    return out


def _validate_holder(payload):
    holder = str(payload.get("holder", "")).strip()

    if not holder:
        raise HTTPException(400, "holder is required")

    if len(holder) > 100:
        raise HTTPException(
            400,
            "holder must be 100 characters or fewer",
        )

    return holder


@app.get("/events")
def events():
    return {
        "events": db.query(
            "SELECT e.id, e.name, e.venue, e.starts_at, e.base_price,"
            " e.row_count * e.seats_per_row AS capacity,"
            " (SELECT count(*) FROM bookings b WHERE b.event_id=e.id) AS sold"
            " FROM events e ORDER BY e.starts_at"
        )
    }


@app.get("/events/{event_id}/seats")
def seats(event_id: int):
    event = _event(event_id)
    sold, held = _sold(event_id), _held(event_id)
    free = [
        s for s in _all_seats(event)
        if s not in sold and s not in held
    ]

    return {
        "event": event["name"],
        "base_price": float(event["base_price"]),
        "available": free,
        "sold": sorted(sold),
        "held": held,
        "hold_seconds": HOLD_SECONDS,
    }


@app.post("/events/{event_id}/hold", status_code=201)
def hold(event_id: int, payload: dict = Body(...)):
    """Put seats on a ten-minute clock. The TTL is the clock - nothing in this
    app has to remember to come back and sweep them up."""
    event = _event(event_id)
    holder = _validate_holder(payload)

    wanted = payload.get("seats")

    if wanted:
        try:
            wanted = [
                seat_label(*parse_seat(s))
                for s in wanted
            ]
        except SeatError as e:
            raise HTTPException(400, str(e))
    else:
        try:
            count = int(payload.get("count", 1))
        except (TypeError, ValueError):
            raise HTTPException(400, "count must be a number")

        if count < 1:
            raise HTTPException(400, "count must be at least 1")

        free = seats(event_id)["available"]
        wanted = find_block(free, count)

        if not wanted:
            raise HTTPException(
                409,
                f"no block of {count} seats together",
            )

    sold = _sold(event_id)

    if any(s in sold for s in wanted):
        raise HTTPException(
            409,
            "one of those seats is already sold",
        )

    on_map = set(_all_seats(event))
    missing = [s for s in wanted if s not in on_map]

    if missing:
        raise HTTPException(
            400,
            f"{missing[0]} is not in this venue",
        )

    c = cache.client()
    taken = []

    for seat in wanted:
        if c.set(
            _hold_key(event_id, seat),
            holder,
            nx=True,
            ex=HOLD_SECONDS,
        ):
            taken.append(seat)
        else:
            # Lost the race on one seat - give back the ones we grabbed so a
            # failed hold leaves no seat stranded until its TTL runs out.
            if taken:
                c.delete(
                    *[_hold_key(event_id, s) for s in taken]
                )

            raise HTTPException(
                409,
                f"{seat} is already held",
            )

    held_at = time.time()
    price = quote(wanted, float(event["base_price"]))

    return {
        "event_id": event_id,
        "holder": holder,
        "seats": wanted,
        "expires_in": seconds_left(
            held_at,
            held_at,
            HOLD_SECONDS,
        ),
        "quote": price,
    }


@app.get("/events/{event_id}/holds/{holder}")
def my_hold(event_id: int, holder: str):
    c = cache.client()
    mine = [
        s for s, h in _held(event_id).items()
        if h == holder
    ]

    if not mine:
        raise HTTPException(404, "no live hold for that holder")

    ttls = [c.ttl(_hold_key(event_id, s)) for s in mine]

    return {
        "event_id": event_id,
        "holder": holder,
        "seats": sorted(mine),
        "expires_in": max(0, min(t for t in ttls if t is not None)),
    }


@app.post("/events/{event_id}/confirm")
def confirm(event_id: int, payload: dict = Body(...)):
    """Turn a live hold into a booking, or fail because it expired."""
    event = _event(event_id)
    holder = _validate_holder(payload)

    mine = sorted(
        s for s, h in _held(event_id).items()
        if h == holder
    )

    if not mine:
        raise HTTPException(
            409,
            "that hold has expired or never existed",
        )

    keys = [_hold_key(event_id, s) for s in mine]
    won = cache.client().eval(
        CONFIRM_LUA,
        len(keys),
        *keys,
        holder,
    )

    if not won:
        raise HTTPException(
            409,
            "that hold expired while you were paying",
        )

    ref = uuid4().hex[:10].upper()
    price = quote(mine, float(event["base_price"]))

    try:
        with db.connect() as conn, conn.cursor() as cur:
            for line in price["lines"]:
                cur.execute(
                    "INSERT INTO bookings (event_id, seat, holder, price, ref)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (
                        event_id,
                        line["seat"],
                        holder,
                        line["price"],
                        ref,
                    ),
                )
    except Exception:
        # UNIQUE(event_id, seat) is the last word. If we get here the hold was
        # honoured but the seat was already sold, so nothing is booked.
        raise HTTPException(409, "one of those seats was sold first")

    return {
        "ref": ref,
        "event": event["name"],
        "holder": holder,
        "seats": mine,
        "quote": price,
        "status": "confirmed",
    }


@app.delete("/events/{event_id}/holds/{holder}")
def release(event_id: int, holder: str):
    mine = [
        s for s, h in _held(event_id).items()
        if h == holder
    ]

    if not mine:
        raise HTTPException(
            404,
            "no live hold for that holder",
        )

    cache.drop(
        *[_hold_key(event_id, s) for s in mine]
    )

    return {
        "event_id": event_id,
        "holder": holder,
        "released": sorted(mine),
    }


@app.get("/bookings/{ref}")
def booking(ref: str):
    rows = db.query(
        "SELECT b.seat, b.price, b.holder, b.created_at, e.name, e.starts_at"
        " FROM bookings b JOIN events e ON e.id=b.event_id"
        " WHERE b.ref=%s ORDER BY b.seat",
        (ref,),
    )

    if not rows:
        raise HTTPException(404, "no such booking")

    return {
        "ref": ref,
        "event": rows[0]["name"],
        "starts_at": rows[0]["starts_at"],
        "holder": rows[0]["holder"],
        "seats": [r["seat"] for r in rows],
        "total": round(sum(float(r["price"]) for r in rows), 2),
    }
