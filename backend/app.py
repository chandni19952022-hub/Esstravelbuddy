"""
ESS Travel Buddy backend
REST API + Server-Sent Events for live presence, groups, chat, and feedback.
Also serves the frontend from ../frontend.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.request
from urllib.parse import quote, urlencode
from pathlib import Path
from uuid import uuid4

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

app = Flask(__name__, static_folder=None)
CORS(app)

AUTO_SHARE_THRESHOLD = 3
PRESENCE_EXPIRY_MS = 3 * 60 * 60 * 1000

_lock = threading.RLock()
_store = {
    "presence": {},      # empId -> dict
    "groups": {},        # id -> dict
    "chat": [],          # list of dicts
    "feedback": [],      # list of dicts
    "users": {},         # empId -> { name, password_hash }
    "profiles": {},      # empId -> commute preferences
    "rideRequests": {},  # id -> ride interest request
}
_version = 0
_condition = threading.Condition(_lock)
_place_cache = {}
_search_cache = {}


def _clip_text(value, limit: int = 80) -> str:
    text = value if isinstance(value, str) else ""
    return text.strip()[:limit]


def _format_place(data: dict) -> str:
    """Turn a map lookup into the place name at that pin: landmark, sector, city."""
    addr = data.get("address") or {}
    display = data.get("display_name") or ""
    sector_match = re.search(r"Sector[- ]?\d+[A-Za-z]?", display, re.IGNORECASE)
    sector = ""
    if sector_match:
        sector = re.sub(r"(?i)^sector[- ]?", "Sector ", sector_match.group(0)).strip()
        sector = re.sub(r"\s+", " ", sector)
    city = addr.get("city") or addr.get("town") or ""
    road = (addr.get("road") or "").strip()
    first = display.split(",")[0].strip() if display else ""
    landmark = ""
    if first and first.lower() not in {road.lower(), city.lower(), sector.lower()} and not first.lower().startswith("sector"):
        landmark = first
    parts = []
    for part in (landmark, sector, city):
        if part and all(part.lower() != existing.lower() for existing in parts):
            parts.append(part)
    if not parts and road:
        parts.append(road)
        if city and city.lower() != road.lower():
            parts.append(city)
    return ", ".join(parts[:3])


def _lookup_place(lat: float, lon: float) -> str:
    key = f"{round(lat, 4):.4f},{round(lon, 4):.4f}"
    cached = _place_cache.get(key)
    if cached:
        return cached
    url = (
        "https://nominatim.openstreetmap.org/reverse?format=jsonv2&addressdetails=1&zoom=18&lat="
        + quote(str(lat))
        + "&lon="
        + quote(str(lon))
    )
    req = urllib.request.Request(url, headers={"User-Agent": "ESS-Travel-Buddy/1.0 (local commute coordination)"})
    with urllib.request.urlopen(req, timeout=8) as res:
        data = json.loads(res.read().decode("utf-8"))
    place = _format_place(data) or f"{lat:.5f}, {lon:.5f}"
    _place_cache[key] = place
    return place


def _named_ncr_city(props: dict) -> str:
    blob = " ".join(
        str(props.get(field) or "")
        for field in ("city", "county", "district", "locality", "state")
    ).lower()
    if re.search(r"gurgaon|gurugram|sohna|manesar", blob):
        return "Gurugram"
    if re.search(r"faridabad|ballabhgarh", blob):
        return "Faridabad"
    if re.search(r"greater noida|\bnoida\b", blob):
        return "Noida"
    if re.search(r"ghaziabad|indirapuram|vaishali|vasundhara|sahibabad", blob):
        return "Ghaziabad"
    if re.search(r"\bdelhi\b", blob):
        return "Delhi"
    return ""


def _box_ncr_city(lon: float, lat: float) -> str:
    areas = (
        ("Gurugram", 76.78, 28.22, 77.18, 28.55),
        ("Faridabad", 77.22, 28.28, 77.42, 28.50),
        ("Noida", 77.30, 28.35, 77.65, 28.63),
        ("Ghaziabad", 77.28, 28.62, 77.55, 28.78),
        ("Delhi", 76.84, 28.48, 77.34, 28.89),
    )
    for name, min_lon, min_lat, max_lon, max_lat in areas:
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            return name
    return ""


def _in_ncr(lon: float, lat: float, props: dict) -> str:
    named = _named_ncr_city(props)
    boxed = _box_ncr_city(lon, lat)
    if named and boxed:
        return named
    return boxed or named


def _search_places(query: str, lat: float | None, lon: float | None) -> list[str]:
    """Suggestions only in Delhi, Gurugram, Noida, Ghaziabad, and Faridabad."""
    q = " ".join(query.split())
    if len(q) < 2:
        return []
    key = q.lower()
    cached = _search_cache.get(key)
    now = time.time()
    if cached and now - cached[0] < 600:
        return cached[1]
    params = {
        "q": q,
        "limit": "50",
        "lang": "en",
        "bbox": "76.78,28.22,77.65,28.89",
    }
    url = "https://photon.komoot.io/api/?" + urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ESS-Travel-Buddy/1.0 (local commute coordination)"})
    with urllib.request.urlopen(req, timeout=8) as res:
        data = json.loads(res.read().decode("utf-8"))
    tokens = [part for part in q.lower().split() if len(part) > 1]
    places: list[str] = []
    seen = set()
    for feature in data.get("features") or []:
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        props = feature.get("properties") or {}
        area = _in_ncr(float(coords[0]), float(coords[1]), props)
        if not area:
            continue
        blob = " ".join(
            str(props.get(field) or "")
            for field in ("name", "street", "district", "locality", "city", "state")
        ).lower()
        name = str(props.get("name") or "").lower()
        if tokens and (any(token not in blob for token in tokens) or (name and tokens[0] not in name)):
            continue
        locality = props.get("district") or props.get("locality") or props.get("city") or props.get("street")
        pin = re.sub(r"\D", "", str(props.get("postcode") or ""))
        parts = []
        for part in (props.get("name"), locality, area):
            text = str(part or "").strip()
            if text and all(text.lower() != existing.lower() for existing in parts):
                parts.append(text)
        label = ", ".join(parts[:3])
        if pin and pin not in label:
            label = label + " " + pin
        token = label.lower()
        if not label or token in seen:
            continue
        seen.add(token)
        places.append(label)
    _search_cache[key] = (now, places)
    if len(_search_cache) > 200:
        for old in sorted(_search_cache, key=lambda item: _search_cache[item][0])[:40]:
            _search_cache.pop(old, None)
    return places


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bump() -> None:
    global _version
    _version += 1
    _condition.notify_all()


def _prune_presence_locked() -> None:
    cutoff = _now_ms() - PRESENCE_EXPIRY_MS
    stale = [k for k, v in _store["presence"].items() if v.get("checkinTime", 0) < cutoff]
    for k in stale:
        del _store["presence"][k]


def snapshot() -> dict:
    with _lock:
        _prune_presence_locked()
        return {
            "version": _version,
            "presence": dict(_store["presence"]),
            "groups": [{"id": gid, **g} for gid, g in _store["groups"].items()],
            "chat": list(_store["chat"][-100:]),
            "feedback": list(_store["feedback"]),
            "profiles": dict(_store["profiles"]),
            "rideRequests": [{"id": rid, **r} for rid, r in _store["rideRequests"].items()],
        }


# ---------- REST ----------

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "ess-travel-buddy"})


@app.post("/api/admin/reset")
def admin_reset():
    """Wipe all in-memory data so demos start with a clean empty app (no leftover users/chats)."""
    with _lock:
        _store["presence"].clear()
        _store["groups"].clear()
        _store["chat"].clear()
        _store["feedback"].clear()
        _store["users"].clear()
        _store["profiles"].clear()
        _store["rideRequests"].clear()
        _bump()
    return jsonify({"ok": True, "cleared": True})


@app.post("/api/auth/login")
def auth_login():
    """First login with a new Employee ID creates the account; later logins need the same password."""
    body = request.get_json(force=True, silent=True) or {}
    emp_id = str(body.get("empId") or "").strip()
    name = str(body.get("name") or "").strip()
    password = str(body.get("password") or "")
    if not emp_id or not name or not password:
        return jsonify({"error": "Employee ID, display name, and password are required."}), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400

    password_hash = _hash_password(password)
    with _lock:
        existing = _store["users"].get(emp_id)
        if existing:
            if existing["password_hash"] != password_hash:
                return jsonify({"error": "Wrong password for this Employee ID."}), 401
            existing["name"] = name
        else:
            _store["users"][emp_id] = {"name": name, "password_hash": password_hash}

    return jsonify({"ok": True, "empId": emp_id, "name": name})


@app.post("/api/auth/reset-password")
def auth_reset_password():
    """Replace the password for an existing Employee ID so a forgotten password can be set again."""
    body = request.get_json(force=True, silent=True) or {}
    emp_id = str(body.get("empId") or "").strip()
    new_password = str(body.get("newPassword") or "")
    confirm = str(body.get("confirmPassword") or "")
    if not emp_id or not new_password or not confirm:
        return jsonify({"error": "Employee ID, new password, and confirmation are required."}), 400
    if len(new_password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400
    if new_password != confirm:
        return jsonify({"error": "New password and confirmation do not match."}), 400

    with _lock:
        existing = _store["users"].get(emp_id)
        if not existing:
            return jsonify({"error": "No account found for this Employee ID. Log in once to create it."}), 404
        existing["password_hash"] = _hash_password(new_password)

    return jsonify({"ok": True, "empId": emp_id})


@app.get("/api/snapshot")
def get_snapshot():
    return jsonify(snapshot())


@app.get("/api/place")
def place_at_pin():
    """Place name for an exact map pin, including sector when the map has one."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required."}), 400
    try:
        place = _lookup_place(lat, lon)
    except Exception:
        place = f"{lat:.5f}, {lon:.5f}"
    return jsonify({"place": place, "lat": lat, "lon": lon})


@app.get("/api/places")
def search_places():
    """Suggestions for a destination the user is typing."""
    q = str(request.args.get("q") or "").strip()
    if len(q) < 2 or len(q) > 80:
        return jsonify({"places": []})
    lat = lon = None
    try:
        if request.args.get("lat") not in (None, "") and request.args.get("lon") not in (None, ""):
            lat = float(request.args.get("lat"))
            lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        lat = lon = None
    try:
        places = _search_places(q, lat, lon)
    except Exception:
        places = []
    return jsonify({"places": places})


@app.put("/api/presence/<emp_id>")
def upsert_presence(emp_id: str):
    body = request.get_json(force=True, silent=True) or {}
    lat = body.get("lat")
    lon = body.get("lon")
    place = str(body.get("place") or "").strip()
    if not place and lat is not None and lon is not None:
        try:
            place = _lookup_place(float(lat), float(lon))
        except (TypeError, ValueError, Exception):
            place = ""
    record = {
        "empId": emp_id,
        "name": body.get("name", ""),
        "lat": lat,
        "lon": lon,
        "place": place,
        "checkinTime": body.get("checkinTime") or _now_ms(),
    }
    with _lock:
        _store["presence"][emp_id] = record
        _bump()
    return jsonify(record)


@app.delete("/api/presence/<emp_id>")
def delete_presence(emp_id: str):
    with _lock:
        _store["presence"].pop(emp_id, None)
        _bump()
    return jsonify({"ok": True})


@app.post("/api/groups")
def create_group():
    body = request.get_json(force=True, silent=True) or {}
    gid = uuid4().hex[:12]
    record = {
        "name": body.get("name") or "Ride group",
        "members": list(body.get("members") or []),
        "memberNames": dict(body.get("memberNames") or {}),
        "createdTime": body.get("createdTime") or _now_ms(),
        "status": body.get("status") or "forming",
        "pickupPoint": body.get("pickupPoint") or "OBS Main Gate",
    }
    with _lock:
        _store["groups"][gid] = record
        _bump()
    return jsonify({"id": gid, **record}), 201


@app.get("/api/groups/<group_id>")
def get_group(group_id: str):
    with _lock:
        g = _store["groups"].get(group_id)
        if not g:
            return jsonify({"error": "not found"}), 404
        return jsonify({"id": group_id, **g})


@app.patch("/api/groups/<group_id>")
def update_group(group_id: str):
    body = request.get_json(force=True, silent=True) or {}
    with _lock:
        g = _store["groups"].get(group_id)
        if not g:
            return jsonify({"error": "not found"}), 404
        for key in ("name", "members", "memberNames", "status", "pickupPoint", "completedTime"):
            if key in body:
                g[key] = body[key]
        members = g.get("members") or []
        if g.get("status") not in ("departed", "completed"):
            g["status"] = "ready" if len(members) >= AUTO_SHARE_THRESHOLD else (
                "forming" if g.get("status") == "ready" and len(members) < AUTO_SHARE_THRESHOLD else g.get("status", "forming")
            )
        _bump()
        return jsonify({"id": group_id, **g})


@app.delete("/api/groups/<group_id>")
def delete_group(group_id: str):
    with _lock:
        if group_id not in _store["groups"]:
            return jsonify({"error": "not found"}), 404
        del _store["groups"][group_id]
        _bump()
    return jsonify({"ok": True})


def _match_thread_members(group_id: str) -> set[str] | None:
    """match:<empA>:<empB> is a private thread for one accepted travel-partner pair."""
    if not group_id.startswith("match:"):
        return None
    parts = group_id.split(":")
    if len(parts) != 3 or not parts[1] or not parts[2] or parts[1] == parts[2]:
        return set()
    return {parts[1], parts[2]}


@app.post("/api/chat")
def post_chat():
    body = request.get_json(force=True, silent=True) or {}
    group_id = str(body.get("groupId") or "global")
    sender = str(body.get("empId") or "")
    members = _match_thread_members(group_id)
    if members is not None:
        if sender not in members:
            return jsonify({"error": "Only the matched travel partners can use this chat."}), 403
        with _lock:
            paired = any(
                req.get("status") == "accepted" and {req.get("fromEmpId"), req.get("toEmpId")} == members
                for req in _store["rideRequests"].values()
            )
        if not paired:
            return jsonify({"error": "Chat opens after the travel partners are matched."}), 403
    record = {
        "id": uuid4().hex[:12],
        "empId": sender,
        "name": body.get("name", ""),
        "text": body.get("text", ""),
        "time": body.get("time") or _now_ms(),
        "groupId": group_id,
    }
    with _lock:
        _store["chat"].append(record)
        if len(_store["chat"]) > 500:
            _store["chat"] = _store["chat"][-500:]
        _bump()
    return jsonify(record), 201


@app.post("/api/feedback")
def post_feedback():
    body = request.get_json(force=True, silent=True) or {}
    record = {
        "id": uuid4().hex[:12],
        "groupId": body.get("groupId", ""),
        "empId": body.get("empId", ""),
        "rating": body.get("rating", 0),
        "comment": body.get("comment", ""),
        "time": body.get("time") or _now_ms(),
    }
    with _lock:
        _store["feedback"].append(record)
        _bump()
    return jsonify(record), 201


@app.put("/api/profiles/<emp_id>")
def upsert_profile(emp_id: str):
    body = request.get_json(force=True, silent=True) or {}
    photo = body.get("photo")
    if isinstance(photo, str) and len(photo) > 600_000:
        return jsonify({"error": "Photo too large. Use a smaller image."}), 400

    with _lock:
        prev = _store["profiles"].get(emp_id) or {}
        record = {
            "empId": emp_id,
            "name": body.get("name") or prev.get("name") or "",
            "route": body.get("route") or prev.get("route") or "obs_to_lotus",
            "routeFrom": _clip_text(body.get("routeFrom") if "routeFrom" in body else prev.get("routeFrom"), 120),
            "routeTo": _clip_text(body.get("routeTo") if "routeTo" in body else prev.get("routeTo"), 120),
            "vehicle": body.get("vehicle") or prev.get("vehicle") or "Car",
            "timing": body.get("timing") or prev.get("timing") or "9:30 AM – 5:30 PM",
            "updatedAt": body.get("updatedAt") or _now_ms(),
        }
        if "photo" in body:
            record["photo"] = photo or None
        elif prev.get("photo"):
            record["photo"] = prev["photo"]
        _store["profiles"][emp_id] = record
        _bump()
    return jsonify(record)


@app.post("/api/ride-requests")
def create_ride_request():
    body = request.get_json(force=True, silent=True) or {}
    from_id = str(body.get("fromEmpId") or "").strip()
    to_id = str(body.get("toEmpId") or "").strip()
    if not from_id or not to_id or from_id == to_id:
        return jsonify({"error": "fromEmpId and toEmpId are required."}), 400

    with _lock:
        for rid_existing, existing in _store["rideRequests"].items():
            if (
                existing.get("status") == "pending"
                and existing.get("fromEmpId") == from_id
                and existing.get("toEmpId") == to_id
            ):
                return jsonify({"id": rid_existing, **existing}), 200

        rid = uuid4().hex[:12]
        record = {
            "fromEmpId": from_id,
            "fromName": body.get("fromName") or from_id,
            "toEmpId": to_id,
            "toName": body.get("toName") or to_id,
            "status": "pending",
            "createdTime": body.get("createdTime") or _now_ms(),
        }
        _store["rideRequests"][rid] = record
        _bump()
    return jsonify({"id": rid, **record}), 201


@app.patch("/api/ride-requests/<req_id>")
def update_ride_request(req_id: str):
    body = request.get_json(force=True, silent=True) or {}
    with _lock:
        req = _store["rideRequests"].get(req_id)
        if not req:
            return jsonify({"error": "not found"}), 404
        if "status" in body:
            req["status"] = body["status"]
        if "groupId" in body:
            req["groupId"] = body["groupId"]
        _bump()
        return jsonify({"id": req_id, **req})


@app.delete("/api/ride-requests/<req_id>")
def delete_ride_request(req_id: str):
    with _lock:
        if req_id not in _store["rideRequests"]:
            return jsonify({"error": "not found"}), 404
        del _store["rideRequests"][req_id]
        _bump()
    return jsonify({"ok": True})


@app.get("/api/events")
def events():
    """Server-Sent Events stream — clients get a full snapshot whenever data changes."""

    def stream():
        last = -1
        while True:
            with _condition:
                while _version == last:
                    _condition.wait(timeout=25)
                last = _version
                data = snapshot()
            yield f"data: {json.dumps(data)}\n\n"

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(stream(), mimetype="text/event-stream", headers=headers)


# ---------- Frontend static ----------

@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:path>")
def static_files(path: str):
    target = FRONTEND / path
    if target.is_file():
        return send_from_directory(FRONTEND, path)
    return send_from_directory(FRONTEND, "index.html")


def _lan_urls(port: int) -> list[str]:
    """Collect non-loopback IPv4 addresses so phones/laptops on the same Wi‑Fi can connect."""
    import socket

    urls = [f"https://127.0.0.1:{port}"]
    seen = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in seen and not ip.startswith("169.254."):
                seen.add(ip)
                urls.append(f"https://{ip}:{port}")
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        probe.close()
        if ip not in seen:
            urls.append(f"https://{ip}:{port}")
    except OSError:
        pass
    return urls


def _ensure_dev_cert(hosts: list[str]) -> tuple[str, str, str]:
    """Local CA plus a server certificate so browsers can open https and use GPS."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_dir = Path(__file__).resolve().parent / "certs"
    cert_dir.mkdir(exist_ok=True)
    cert_path = cert_dir / "dev-cert.pem"
    key_path = cert_dir / "dev-key.pem"
    ca_path = cert_dir / "dev-ca.pem"
    ca_key_path = cert_dir / "dev-ca-key.pem"
    stamp_path = cert_dir / "dev-hosts.txt"
    stamp = "v3\n" + "\n".join(hosts)
    if (
        cert_path.is_file()
        and key_path.is_file()
        and ca_path.is_file()
        and stamp_path.is_file()
        and stamp_path.read_text(encoding="utf-8") == stamp
    ):
        return str(cert_path), str(key_path), str(ca_path)

    def _load_or_create_ca():
        if ca_path.is_file() and ca_key_path.is_file():
            ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
            ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())
            return ca_key, ca_cert
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ESS Travel Buddy Dev CA")])
        now = datetime.datetime.now(datetime.timezone.utc)
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        ca_key_path.write_bytes(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        return ca_key, ca_cert

    ca_key, ca_cert = _load_or_create_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ESS Travel Buddy")])
    alt_names = [x509.DNSName("localhost")]
    for host in hosts:
        host = host.split("://", 1)[-1].rsplit(":", 1)[0]
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            if host and all(isinstance(name, x509.DNSName) and name.value != host for name in alt_names):
                alt_names.append(x509.DNSName(host))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=397))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM))
    stamp_path.write_text(stamp, encoding="utf-8")
    return str(cert_path), str(key_path), str(ca_path)


def _trust_dev_ca(ca_file: str) -> None:
    """Trust the local CA for this Windows user so https opens without a warning."""
    import subprocess

    try:
        subprocess.run(
            ["certutil", "-user", "-addstore", "Root", ca_file],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        pass


def _run_https(port: int, cert_file: str, key_file: str) -> None:
    """Serve HTTP and HTTPS on the same port.

    TLS is finished on the worker thread. A stalled handshake must not block
    the next connection, and http://127.0.0.1 still has to open.
    """
    import socket
    import ssl

    from werkzeug.serving import ThreadedWSGIServer, WSGIRequestHandler

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)

    class _Handler(WSGIRequestHandler):
        def make_environ(self):
            environ = super().make_environ()
            environ["wsgi.url_scheme"] = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
            return environ

    class _Server(ThreadedWSGIServer):
        def process_request_thread(self, request, client_address):  # type: ignore[override]
            try:
                request.settimeout(8)
                peeked = request.recv(1, socket.MSG_PEEK)
                if peeked[:1] == b"\x16":
                    conn = self.ssl_context.wrap_socket(request, server_side=True)
                else:
                    conn = request
                conn.settimeout(None)
            except Exception:
                try:
                    request.close()
                except Exception:
                    pass
                return
            try:
                self.finish_request(conn, client_address)
            except Exception:
                self.handle_error(conn, client_address)
            finally:
                self.shutdown_request(conn)

    server = _Server("0.0.0.0", port, app, _Handler)
    server.ssl_context = ctx
    server.log_startup()
    server.serve_forever()


if __name__ == "__main__":
    port = int(__import__("os").environ.get("PORT", "5000"))
    urls = _lan_urls(port)
    cert_file, key_file, ca_file = _ensure_dev_cert(urls)
    _trust_dev_ca(ca_file)
    print("\n  ESS Travel Buddy")
    print("  Listening on all interfaces (0.0.0.0) — same Wi‑Fi / LAN pe open karo:\n")
    print(f"    http://127.0.0.1:{port}")
    for url in urls:
        print(f"    {url}")
    print("\n  Is PC pe http://127.0.0.1 bhi chalta hai.")
    print("  Colleague ke system pe https Network URL kholo, phir location Allow karo.")
    print("  Phone/laptop: is PC ke saath same Wi‑Fi use karo.\n")
    _run_https(port, cert_file, key_file)
