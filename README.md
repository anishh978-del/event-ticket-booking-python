# Event Ticket Booking

**Language:** Python (FastAPI) &nbsp;|&nbsp; **Needs:** Postgres + Redis

This is a **starter**. The application already works. Your job is everything
that gets it building, tested and running in CI.

---

## You do not need Python installed

You will build this into a container, and the container brings its own
Python 3.12. You are not being asked to extend the app — you are being asked
to ship it.

---

## 1. What this app needs

| | |
|---|---|
| **Runtime** | Python 3.12 |
| **Install dependencies** | `pip install -r requirements.txt` |
| **Start the app** | `uvicorn app.main:app --host 0.0.0.0 --port 8080` |
| **Listens on** | port 8080, bound to `0.0.0.0` |
| **Environment variables** | `DATABASE_URL`, `REDIS_URL`; optionally `HOLD_SECONDS` (default 600) |
| **Needs running first** | Postgres, Redis, and the migrations applied |

### What it does

Browse events, pick seats or ask for N together, hold them for ten minutes while you pay, then confirm. A hold nobody confirms expires on its own and the seats go back on sale. Row tiers set the price and the booking fee is charged once per order.

### Endpoints

```
GET    /health
GET    /events                          what is on, capacity and how much is sold
GET    /events/{id}/seats               available / sold / currently held
POST   /events/{id}/hold                {"holder":"asha","seats":["B3","B4"]}
                                        or {"holder":"asha","count":3} to be seated together
GET    /events/{id}/holds/{holder}      seconds left on your hold
DELETE /events/{id}/holds/{holder}      give them back early
POST   /events/{id}/confirm             {"holder":"asha"}  -> a booking reference
GET    /bookings/{ref}                  what you actually bought
```

`/health` reports Postgres and Redis **separately**. If it says
`postgres: false` the app started fine and your compose wiring is wrong —
do not go looking in the application code.

### Migrations

`migrations/` holds `.sql` files applied **in filename order** before the app
starts. They create the tables and insert sample data. A container running
`psql` over them in order is enough; you do not need a migration tool.

---

## 2. What you must write

| File | What it has to do |
|---|---|
| `Dockerfile` | Install dependencies **before** copying source, pin the base image, do not run as root. |
| `docker-compose.yml` | App + Postgres + Redis + a migration step, one `docker compose up`. |
| `.circleci/config.yml` | lint → unit tests → integration tests → secret scan → image build |
| Unit tests | For `app/seats.py`. No database, no network. |
| Integration tests | Against a real Postgres and Redis as CircleCI service containers. |

Then push your image to **your own Docker Hub account**, tagged `:1.0`.

### When it works

```bash
docker compose up --build
curl localhost:8080/health
```

```json
{"status":"ok","postgres":true,"redis":true}
```

---

## Where the marks are

`app/seats.py` is **pure logic** — plain functions over plain data, no
database and no HTTP. Start your tests there. Use pytest:
`pytest --cov=app --cov-report=term-missing`. Minimum 70%.

`hold_is_live(held_at, now)` is deliberately strict at the boundary: at exactly `held_at + 600` the hold is already dead. Write that test first and decide whether you agree.

## Why Redis is here

A hold is a key, `hold:{event}:{seat}`, set with SET NX EX. The NX is the lock - two people cannot both create the same key - and the EX is the ten-minute timer. Nothing in the application sweeps expired holds, because there is nothing to sweep: the key stops existing and the seat is free again. Confirming runs a small Lua script so the check that every seat is still yours and the delete of those keys happen as one operation.

## The hard part

Expiry must be exact. A seat released one second late is a seat sold twice.

Work out where the ten minutes is actually enforced. It is not in Python - `seconds_left` only reports. It is the `ex=HOLD_SECONDS` on the Redis key, and Redis is the single clock everyone reads, which is why this works with more than one copy of the app running and a `datetime.now()` comparison in Python would not. Then find the gap this still leaves: between confirm reading the holds and confirm writing the booking rows, the TTL can lapse. Explain what the Lua script buys you there, what it does not, and why `UNIQUE(event_id, seat)` is the only thing that can truly say no. Set HOLD_SECONDS=5 in compose and prove your answer.

Write your answer in your README. It is worth more marks than the feature.

---

## Getting unstuck

| Symptom | Almost always |
|---|---|
| `/health` says `postgres: false` | Wrong hostname. In compose the host is the **service name**, not `localhost`. |
| Page will not load, logs fine | No `ports:` mapping, or bound to `127.0.0.1` not `0.0.0.0`. |
| `relation "..." does not exist` | Migrations did not run, or the app started before they finished. |
| Build takes minutes each time | `COPY . .` is above your dependency install. |
| CI cannot reach the database | In CircleCI service containers the host **is** `localhost` — opposite of compose. |
## Deployment Workflow

This project uses three deployment branches:

- `main`: QA environment
- `staging`: Pre-production environment
- `prod`: Production environment

Changes are introduced through feature branches and pull requests.
Promotions between environments are performed through pull requests