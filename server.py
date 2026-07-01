import os
from typing import Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

JETLOG_BASE_URL = os.environ.get("JETLOG_BASE_URL", "http://jetlog.jetlog.svc.cluster.local:3000")
JETLOG_API_KEY = os.environ["JETLOG_API_KEY"]
MCP_TOKEN = os.environ.get("MCP_TOKEN")
PORT = int(os.environ.get("PORT", "8080"))

INSTRUCTIONS = """\
Tools for a personal jetlog flight log (https://jetlog.internal.white.fm).

Logging a flight from an email / booking confirmation / boarding pass:
  1. If you have a boarding-pass barcode string, call parse_boarding_pass to extract the fields.
  2. Resolve airports to ICAO (4-letter) or IATA (3-letter) codes with search_airports if unsure.
  3. Call check_duplicate(date, origin, destination) and skip if it reports a match.
  4. Call add_flight with the resolved fields.
Dates are YYYY-MM-DD, times are HH:MM (24h, local). origin/destination are airport codes.

Companions (family members / travel partners):
  - add_flight accepts a `companions` list of names; or attach them afterwards with
    set_flight_companions (one flight) / add_flight_companions (many flights at once).
  - New names auto-create a profile. list_companions and get_companion show who you fly
    with and every flight you've shared with them.

Enriching a flight (when logging, or later on an existing flight):
  - Plane details (airplane type like "B738", tail_number) and flight_number can be passed
    to add_flight, or added to an existing flight with update_flight.
  - Frequent flyer: attach a loyalty program + membership number (and optional miles /
    status credits) with set_frequent_flyer; frequent_flyer_summary totals them across flights.
"""

mcp = FastMCP("jetlog", instructions=INSTRUCTIONS, host="0.0.0.0", port=PORT)

_client = httpx.AsyncClient(
    base_url=JETLOG_BASE_URL,
    headers={"Authorization": f"Bearer {JETLOG_API_KEY}"},
    timeout=30.0,
)

# jetlog's API uses camelCase keys; tool args are snake_case for ergonomics.
_FIELD_ALIASES = {
    "departure_time": "departureTime",
    "arrival_time": "arrivalTime",
    "arrival_date": "arrivalDate",
    "seat_number": "seatNumber",
    "aircraft_side": "aircraftSide",
    "ticket_class": "ticketClass",
    "tail_number": "tailNumber",
    "flight_number": "flightNumber",
}


async def _req(method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
    resp = await _client.request(method, path, params=params, json=json)
    if resp.status_code >= 400:
        raise RuntimeError(f"jetlog {method} {path} -> {resp.status_code}: {resp.text[:500]}")
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    return resp.text


def _flight_body(fields: dict) -> dict:
    body: dict = {}
    for key, value in fields.items():
        if value is None:
            continue
        body[_FIELD_ALIASES.get(key, key)] = value
    return body


@mcp.tool()
async def list_flights(
    limit: int = 20,
    offset: int = 0,
    start: str | None = None,
    end: str | None = None,
    origin: str | None = None,
    destination: str | None = None,
    order: str = "DESC",
    sort: str = "date",
) -> list[dict]:
    """List logged flights, newest first by default.

    start/end are YYYY-MM-DD bounds on the flight date. origin/destination filter by
    airport ICAO/IATA code. order is "DESC" or "ASC"; sort is one of
    date, seat, aircraft_side, ticket_class, duration, distance.
    """
    params: dict = {"limit": limit, "offset": offset, "order": order, "sort": sort, "metric": True}
    for k, v in (("start", start), ("end", end), ("origin", origin), ("destination", destination)):
        if v:
            params[k] = v
    return await _req("GET", "/api/flights", params=params)


@mcp.tool()
async def get_flight(flight_id: int) -> dict:
    """Fetch a single flight by its id."""
    return await _req("GET", "/api/flights", params={"id": flight_id, "metric": True})


@mcp.tool()
async def add_flight(
    date: str,
    origin: str,
    destination: str,
    departure_time: str | None = None,
    arrival_time: str | None = None,
    arrival_date: str | None = None,
    flight_number: str | None = None,
    airline: str | None = None,
    airplane: str | None = None,
    tail_number: str | None = None,
    seat: str | None = None,
    seat_number: str | None = None,
    aircraft_side: str | None = None,
    ticket_class: str | None = None,
    purpose: str | None = None,
    duration: int | None = None,
    distance: int | None = None,
    cost: float | None = None,
    currency: str | None = None,
    rating: int | None = None,
    connection: int | None = None,
    notes: str | None = None,
    companions: list[str] | None = None,
) -> dict:
    """Add a flight to the log.

    Required: date (YYYY-MM-DD), origin and destination as ICAO (4-letter) or IATA
    (3-letter) airport codes. Times are HH:MM (24h, local).
    Enum fields:
      seat:          window | middle | aisle
      aircraft_side: left | right | center
      ticket_class:  private | first | business | economy+ | economy
      purpose:       leisure | business | crew | other
    airline must be a valid airline ICAO code that exists in jetlog (e.g. "UAL",
    "BAW") — resolve it with search_airlines first; free text is rejected.
    duration is minutes, distance is km. connection is the flight id of the onward /
    connecting flight this leg links to (usually left unset — jetlog auto-detects
    connections). companions is an optional list of family-
    member / travel-partner names who were on the flight; unknown names auto-create a
    profile. When logging from an email/confirmation, call check_duplicate first.
    Returns {"id": <new flight id>}.
    """
    fields = dict(locals())
    companion_names = fields.pop("companions", None)
    body = _flight_body(fields)
    new_id = await _req("POST", "/api/flights", json=body)
    if companion_names:
        await _req("POST", f"/api/flights/{new_id}/companions", json={"names": companion_names})
    return {"id": new_id}


@mcp.tool()
async def update_flight(
    flight_id: int,
    date: str | None = None,
    origin: str | None = None,
    destination: str | None = None,
    departure_time: str | None = None,
    arrival_time: str | None = None,
    arrival_date: str | None = None,
    flight_number: str | None = None,
    airline: str | None = None,
    airplane: str | None = None,
    tail_number: str | None = None,
    seat: str | None = None,
    seat_number: str | None = None,
    aircraft_side: str | None = None,
    ticket_class: str | None = None,
    purpose: str | None = None,
    duration: int | None = None,
    distance: int | None = None,
    cost: float | None = None,
    currency: str | None = None,
    rating: int | None = None,
    connection: int | None = None,
    notes: str | None = None,
) -> dict:
    """Update fields on an existing flight. Only the fields you pass are changed.

    Same field meanings and enum values as add_flight.
    """
    fields = {k: v for k, v in locals().items() if k != "flight_id"}
    body = _flight_body(fields)
    result = await _req("PATCH", "/api/flights", params={"id": flight_id}, json=body)
    return {"id": flight_id, "result": result}


@mcp.tool()
async def delete_flight(flight_id: int) -> dict:
    """Delete a flight by id."""
    result = await _req("DELETE", "/api/flights", params={"id": flight_id})
    return {"deleted": flight_id, "result": result}


# ---- Companions (family members / travel partners) ----

@mcp.tool()
async def list_companions() -> list[dict]:
    """List saved companions (family members / travel partners), each with how many
    flights you've taken together, total distance, and the most recent shared flight.
    """
    return await _req("GET", "/api/companions", params={"metric": True})


@mcp.tool()
async def get_companion(companion_id: int) -> dict:
    """Get one companion's profile: stats (flights together, distance, hours, unique
    airports/countries, first/last flight, top destinations) plus the full list of
    flights you've shared with them.
    """
    return await _req("GET", f"/api/companions/{companion_id}", params={"metric": True})


@mcp.tool()
async def add_companion(name: str, relation: str | None = None, notes: str | None = None) -> dict:
    """Create a companion profile ahead of time. relation is free text (e.g. spouse,
    child, parent, friend). Companions are also auto-created whenever you attach a new
    name to a flight, so this is only needed to pre-register someone.
    """
    body: dict = {"name": name}
    if relation:
        body["relation"] = relation
    if notes:
        body["notes"] = notes
    return await _req("POST", "/api/companions", json=body)


@mcp.tool()
async def update_companion(
    companion_id: int,
    name: str | None = None,
    relation: str | None = None,
    notes: str | None = None,
) -> dict:
    """Rename a companion or change their relation/notes. Only the fields you pass change."""
    body: dict = {}
    for key, value in (("name", name), ("relation", relation), ("notes", notes)):
        if value is not None:
            body[key] = value
    return await _req("PATCH", f"/api/companions/{companion_id}", json=body)


@mcp.tool()
async def delete_companion(companion_id: int) -> dict:
    """Delete a companion. Unlinks them from all flights but does not delete the flights."""
    return await _req("DELETE", f"/api/companions/{companion_id}")


@mcp.tool()
async def get_flight_companions(flight_id: int) -> list[dict]:
    """List the companions attached to a single flight."""
    return await _req("GET", f"/api/flights/{flight_id}/companions")


@mcp.tool()
async def set_flight_companions(flight_id: int, names: list[str]) -> list[dict]:
    """Set (replace) the companions on a flight by name. Unknown names auto-create a
    profile. Pass an empty list to remove all companions from the flight.
    """
    return await _req("POST", f"/api/flights/{flight_id}/companions", json={"names": names})


@mcp.tool()
async def add_flight_companions(flight_ids: list[int], names: list[str]) -> dict:
    """Add the same companions to many flights at once, keeping each flight's existing
    companions. Unknown names auto-create a profile. Returns {"updated": <count>}.
    """
    return await _req("POST", "/api/companions/bulk-assign", json={"ids": flight_ids, "names": names})


# ---- Frequent flyer ----

@mcp.tool()
async def get_frequent_flyer(flight_id: int) -> dict | None:
    """Get the frequent-flyer entry on a flight (loyalty program, member number, miles,
    status credits), or null if none is set."""
    return await _req("GET", f"/api/flights/{flight_id}/frequent-flyer")


@mcp.tool()
async def set_frequent_flyer(
    flight_id: int,
    program_name: str,
    member_number: str | None = None,
    miles_earned: int = 0,
    status_credits: int = 0,
) -> dict:
    """Add or update the frequent-flyer entry on a flight.

    program_name is the loyalty program (e.g. "United MileagePlus", "Delta SkyMiles")
    and is required. member_number is your frequent-flyer / membership number.
    miles_earned and status_credits (elite-qualifying credits) are optional integers.
    Each flight holds a single entry; calling again replaces it.
    """
    body: dict = {
        "program_name": program_name,
        "miles_earned": miles_earned,
        "status_credits": status_credits,
    }
    if member_number is not None:
        body["member_number"] = member_number
    return await _req("POST", f"/api/flights/{flight_id}/frequent-flyer", json=body)


@mcp.tool()
async def delete_frequent_flyer(flight_id: int) -> dict:
    """Remove the frequent-flyer entry from a flight."""
    return await _req("DELETE", f"/api/flights/{flight_id}/frequent-flyer")


@mcp.tool()
async def frequent_flyer_summary() -> dict:
    """Frequent-flyer totals across all flights: per-program miles, status credits and
    flight counts, plus overall totals."""
    return await _req("GET", "/api/frequent-flyer/summary")


@mcp.tool()
async def check_duplicate(date: str, origin: str, destination: str) -> dict:
    """Check whether a flight on this date/route is already logged, before adding one.

    date is YYYY-MM-DD; origin/destination are airport codes.
    """
    return await _req(
        "GET",
        "/api/flights/check-duplicate",
        params={"date": date, "origin": origin, "destination": destination},
    )


@mcp.tool()
async def parse_boarding_pass(raw: str) -> dict:
    """Parse an IATA BCBP boarding-pass barcode string (PDF417/Aztec, >=58 chars)
    into flight fields. Does not log the flight; pass the result to add_flight.
    """
    return await _req("POST", "/api/boarding-pass/parse", json={"raw": raw})


@mcp.tool()
async def enrich_flights() -> dict:
    """Enrich all logged flights that have a flight number but are missing details
    (aircraft type, tail number, real times) using external flight-data APIs.
    Operates on the whole log, not a single flight.
    """
    return await _req("POST", "/api/flights/enrich")


@mcp.tool()
async def search_airports(q: str) -> list[dict]:
    """Search airports by name, city, ICAO, or IATA code. Use to resolve an airport
    to its code before adding a flight.
    """
    return await _req("GET", "/api/airports", params={"q": q})


@mcp.tool()
async def search_airlines(q: str) -> list[dict]:
    """Search airlines by name or ICAO/IATA code."""
    return await _req("GET", "/api/airlines", params={"q": q})


@mcp.tool()
async def get_statistics() -> dict:
    """Overall flight statistics: totals, durations, distances, most-visited airports,
    countries, airlines, seat and class frequencies.
    """
    return await _req("GET", "/api/statistics")


@mcp.tool()
async def get_analytics(kind: str) -> Any:
    """Analytics breakdowns. kind is one of: routes, aircraft, airports, heatmap, tail-numbers."""
    allowed = {"routes", "aircraft", "airports", "heatmap", "tail-numbers"}
    if kind not in allowed:
        raise ValueError(f"kind must be one of {sorted(allowed)}")
    return await _req("GET", f"/api/analytics/{kind}")


class TokenAuth(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if MCP_TOKEN and request.url.path != "/healthz":
            if request.headers.get("authorization", "") != f"Bearer {MCP_TOKEN}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def main() -> None:
    app = mcp.streamable_http_app()
    app.add_middleware(TokenAuth)
    app.add_route("/healthz", lambda _req: PlainTextResponse("ok"), methods=["GET"])
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
