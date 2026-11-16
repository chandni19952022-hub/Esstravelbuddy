import { createHash, randomBytes } from "node:crypto";

const PRESENCE_EXPIRY_MS = 3 * 60 * 60 * 1000;
const AUTO_SHARE_THRESHOLD = 3;

function emptyState() {
  return {
    version: 0,
    presence: {},
    groups: {},
    chat: [],
    feedback: [],
    users: {},
    profiles: {},
    rideRequests: {},
  };
}

function ensureShape(state) {
  state.version = state.version || 0;
  state.presence = state.presence || {};
  state.groups = state.groups || {};
  state.chat = state.chat || [];
  state.feedback = state.feedback || [];
  state.users = state.users || {};
  state.profiles = state.profiles || {};
  state.rideRequests = state.rideRequests || {};
  return state;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function hashPassword(password) {
  return createHash("sha256").update(String(password), "utf8").digest("hex");
}

function nowMs() {
  return Date.now();
}

function newId() {
  return randomBytes(6).toString("hex");
}

function clipText(value, limit = 80) {
  return String(value || "").trim().slice(0, limit);
}

function bump(state) {
  state.version += 1;
}

function prunePresence(state) {
  const cutoff = nowMs() - PRESENCE_EXPIRY_MS;
  for (const [id, row] of Object.entries(state.presence)) {
    if ((row.checkinTime || 0) < cutoff) delete state.presence[id];
  }
}

function publicSnapshot(state) {
  prunePresence(state);
  return {
    version: state.version,
    presence: state.presence,
    groups: Object.entries(state.groups).map(([id, group]) => ({ id, ...group })),
    chat: state.chat.slice(-100),
    feedback: state.feedback,
    profiles: state.profiles,
    rideRequests: Object.entries(state.rideRequests).map(([id, row]) => ({ id, ...row })),
  };
}

function formatPlace(data) {
  const addr = data.address || {};
  const display = data.display_name || "";
  const sectorMatch = display.match(/Sector[- ]?\d+[A-Za-z]?/i);
  let sector = "";
  if (sectorMatch) {
    sector = sectorMatch[0].replace(/^sector[- ]?/i, "Sector ").replace(/\s+/g, " ").trim();
  }
  const city = addr.city || addr.town || "";
  const road = String(addr.road || "").trim();
  const first = display ? display.split(",")[0].trim() : "";
  let landmark = "";
  if (first && first.toLowerCase() !== road.toLowerCase() && first.toLowerCase() !== city.toLowerCase() && first.toLowerCase() !== sector.toLowerCase() && !first.toLowerCase().startsWith("sector")) {
    landmark = first;
  }
  const parts = [];
  for (const part of [landmark, sector, city]) {
    if (part && parts.every((existing) => existing.toLowerCase() !== part.toLowerCase())) parts.push(part);
  }
  if (!parts.length && road) {
    parts.push(road);
    if (city && city.toLowerCase() !== road.toLowerCase()) parts.push(city);
  }
  return parts.slice(0, 3).join(", ");
}

async function lookupPlace(lat, lon) {
  const url = "https://nominatim.openstreetmap.org/reverse?format=jsonv2&addressdetails=1&zoom=18&lat="
    + encodeURIComponent(lat) + "&lon=" + encodeURIComponent(lon);
  const res = await fetch(url, {
    headers: { "User-Agent": "ESS-Travel-Buddy/1.0 (commute coordination)" },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error("place lookup failed");
  const data = await res.json();
  return formatPlace(data) || Number(lat).toFixed(5) + ", " + Number(lon).toFixed(5);
}

function apiPath(request) {
  const url = new URL(request.url);
  const marker = "/api/";
  const index = url.pathname.indexOf(marker);
  const path = index >= 0 ? url.pathname.slice(index) : url.pathname;
  return { path, search: url.searchParams };
}

async function readState(store) {
  const current = await store.getWithMetadata("state", { type: "json", consistency: "strong" });
  return ensureShape(current && current.data ? current.data : emptyState());
}

async function writeState(store, change) {
  for (let attempt = 0; attempt < 6; attempt++) {
    const current = await store.getWithMetadata("state", { type: "json", consistency: "strong" });
    const state = ensureShape(current && current.data ? structuredClone(current.data) : emptyState());
    const outcome = await change(state);
    if (!outcome.write) return outcome.response;
    const saved = await store.setJSON(
      "state",
      state,
      current && current.etag ? { onlyIfMatch: current.etag } : { onlyIfNew: true }
    );
    if (saved && saved.modified) return outcome.response;
  }
  return json({ error: "Could not save. Try again." }, 503);
}

function saved(data, status = 200) {
  return { write: true, response: json(data, status) };
}

function rejected(error, status) {
  return { write: false, response: json({ error }, status) };
}

function matchThreadMembers(groupId) {
  if (!String(groupId).startsWith("match:")) return null;
  const parts = String(groupId).split(":");
  if (parts.length !== 3 || !parts[1] || !parts[2] || parts[1] === parts[2]) return new Set();
  return new Set([parts[1], parts[2]]);
}

export async function handleRequest(request, store) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204 });
  const { path, search } = apiPath(request);
  const method = request.method;
  let body = {};
  if (method !== "GET" && method !== "DELETE") {
    body = await request.json().catch(() => ({}));
    if (!body || typeof body !== "object") body = {};
  }

  if (method === "GET" && path === "/api/health") {
    return json({ ok: true, service: "ess-travel-buddy" });
  }

  if (method === "GET" && path === "/api/snapshot") {
    return json(publicSnapshot(await readState(store)));
  }

  if (method === "GET" && path === "/api/events") {
    const snap = publicSnapshot(await readState(store));
    return new Response("data: " + JSON.stringify(snap) + "\n\n", {
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
    });
  }

  if (method === "GET" && path === "/api/place") {
    const lat = Number(search.get("lat"));
    const lon = Number(search.get("lon"));
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return json({ error: "lat and lon are required." }, 400);
    let place = lat.toFixed(5) + ", " + lon.toFixed(5);
    try { place = await lookupPlace(lat, lon); } catch (err) { /* keep coordinates */ }
    return json({ place, lat, lon });
  }

  if (method === "POST" && path === "/api/auth/login") {
    const empId = String(body.empId || "").trim();
    const name = String(body.name || "").trim();
    const password = String(body.password || "");
    if (!empId || !name || !password) return json({ error: "Employee ID, display name, and password are required." }, 400);
    if (password.length < 4) return json({ error: "Password must be at least 4 characters." }, 400);
    const passwordHash = hashPassword(password);
    return writeState(store, (state) => {
      const existing = state.users[empId];
      if (existing) {
        if (existing.password_hash !== passwordHash) return rejected("Wrong password for this Employee ID.", 401);
        existing.name = name;
      } else {
        state.users[empId] = { name, password_hash: passwordHash };
      }
      bump(state);
      return saved({ ok: true, empId, name });
    });
  }

  if (method === "POST" && path === "/api/auth/reset-password") {
    const empId = String(body.empId || "").trim();
    const newPassword = String(body.newPassword || "");
    const confirm = String(body.confirmPassword || "");
    if (!empId || !newPassword || !confirm) return json({ error: "Employee ID, new password, and confirmation are required." }, 400);
    if (newPassword.length < 4) return json({ error: "Password must be at least 4 characters." }, 400);
    if (newPassword !== confirm) return json({ error: "New password and confirmation do not match." }, 400);
    return writeState(store, (state) => {
      const existing = state.users[empId];
      if (!existing) return rejected("No account found for this Employee ID. Log in once to create it.", 404);
      existing.password_hash = hashPassword(newPassword);
      bump(state);
      return saved({ ok: true, empId });
    });
  }

  const presence = path.match(/^\/api\/presence\/([^/]+)$/);
  if (presence && method === "PUT") {
    const empId = decodeURIComponent(presence[1]);
    let place = String(body.place || "").trim();
    if (!place && body.lat != null && body.lon != null) {
      try { place = await lookupPlace(Number(body.lat), Number(body.lon)); } catch (err) { place = ""; }
    }
    const record = {
      empId,
      name: body.name || "",
      lat: body.lat,
      lon: body.lon,
      place,
      checkinTime: body.checkinTime || nowMs(),
    };
    return writeState(store, (state) => {
      state.presence[empId] = record;
      bump(state);
      return saved(record);
    });
  }
  if (presence && method === "DELETE") {
    const empId = decodeURIComponent(presence[1]);
    return writeState(store, (state) => {
      delete state.presence[empId];
      bump(state);
      return saved({ ok: true });
    });
  }

  if (method === "POST" && path === "/api/groups") {
    const id = newId();
    const record = {
      name: body.name || "Ride group",
      members: Array.isArray(body.members) ? body.members : [],
      memberNames: body.memberNames && typeof body.memberNames === "object" ? body.memberNames : {},
      createdTime: body.createdTime || nowMs(),
      status: body.status || "forming",
      pickupPoint: body.pickupPoint || "OBS Main Gate",
    };
    return writeState(store, (state) => {
      state.groups[id] = record;
      bump(state);
      return saved({ id, ...record }, 201);
    });
  }

  const group = path.match(/^\/api\/groups\/([^/]+)$/);
  if (group && method === "GET") {
    const id = decodeURIComponent(group[1]);
    const state = await readState(store);
    const row = state.groups[id];
    if (!row) return json({ error: "not found" }, 404);
    return json({ id, ...row });
  }
  if (group && method === "PATCH") {
    const id = decodeURIComponent(group[1]);
    return writeState(store, (state) => {
      const row = state.groups[id];
      if (!row) return rejected("not found", 404);
      for (const key of ["name", "members", "memberNames", "status", "pickupPoint", "completedTime"]) {
        if (key in body) row[key] = body[key];
      }
      const members = row.members || [];
      if (row.status !== "departed" && row.status !== "completed") {
        if (members.length >= AUTO_SHARE_THRESHOLD) row.status = "ready";
        else if (row.status === "ready") row.status = "forming";
      }
      bump(state);
      return saved({ id, ...row });
    });
  }
  if (group && method === "DELETE") {
    const id = decodeURIComponent(group[1]);
    return writeState(store, (state) => {
      if (!state.groups[id]) return rejected("not found", 404);
      delete state.groups[id];
      bump(state);
      return saved({ ok: true });
    });
  }

  if (method === "POST" && path === "/api/chat") {
    const groupId = String(body.groupId || "global");
    const sender = String(body.empId || "");
    const members = matchThreadMembers(groupId);
    if (members && !members.has(sender)) {
      return json({ error: "Only the matched travel partners can use this chat." }, 403);
    }
    return writeState(store, (state) => {
      if (members) {
        const paired = Object.values(state.rideRequests).some((req) => (
          req.status === "accepted" && new Set([req.fromEmpId, req.toEmpId]).size === members.size
          && [...members].every((id) => id === req.fromEmpId || id === req.toEmpId)
        ));
        if (!paired) return rejected("Chat opens after the travel partners are matched.", 403);
      }
      const record = {
        id: newId(),
        empId: sender,
        name: body.name || "",
        text: body.text || "",
        time: body.time || nowMs(),
        groupId,
      };
      state.chat.push(record);
      if (state.chat.length > 500) state.chat = state.chat.slice(-500);
      bump(state);
      return saved(record, 201);
    });
  }

  if (method === "POST" && path === "/api/feedback") {
    const record = {
      id: newId(),
      groupId: body.groupId || "",
      empId: body.empId || "",
      rating: body.rating || 0,
      comment: body.comment || "",
      time: body.time || nowMs(),
    };
    return writeState(store, (state) => {
      state.feedback.push(record);
      bump(state);
      return saved(record, 201);
    });
  }

  const profile = path.match(/^\/api\/profiles\/([^/]+)$/);
  if (profile && method === "PUT") {
    const empId = decodeURIComponent(profile[1]);
    if (typeof body.photo === "string" && body.photo.length > 600000) {
      return json({ error: "Photo too large. Use a smaller image." }, 400);
    }
    return writeState(store, (state) => {
      const prev = state.profiles[empId] || {};
      const record = {
        empId,
        name: body.name || prev.name || "",
        route: body.route || prev.route || "obs_to_lotus",
        routeFrom: clipText("routeFrom" in body ? body.routeFrom : prev.routeFrom, 120),
        routeTo: clipText("routeTo" in body ? body.routeTo : prev.routeTo, 120),
        vehicle: body.vehicle || prev.vehicle || "Car",
        timing: body.timing || prev.timing || "9:30 AM – 5:30 PM",
        updatedAt: body.updatedAt || nowMs(),
      };
      if ("photo" in body) record.photo = body.photo || null;
      else if (prev.photo) record.photo = prev.photo;
      state.profiles[empId] = record;
      bump(state);
      return saved(record);
    });
  }

  if (method === "POST" && path === "/api/ride-requests") {
    const fromEmpId = String(body.fromEmpId || "").trim();
    const toEmpId = String(body.toEmpId || "").trim();
    if (!fromEmpId || !toEmpId || fromEmpId === toEmpId) {
      return json({ error: "fromEmpId and toEmpId are required." }, 400);
    }
    return writeState(store, (state) => {
      for (const [id, existing] of Object.entries(state.rideRequests)) {
        if (existing.status === "pending" && existing.fromEmpId === fromEmpId && existing.toEmpId === toEmpId) {
          return saved({ id, ...existing }, 200);
        }
      }
      const id = newId();
      const record = {
        fromEmpId,
        fromName: body.fromName || fromEmpId,
        toEmpId,
        toName: body.toName || toEmpId,
        status: "pending",
        createdTime: body.createdTime || nowMs(),
      };
      state.rideRequests[id] = record;
      bump(state);
      return saved({ id, ...record }, 201);
    });
  }

  const ride = path.match(/^\/api\/ride-requests\/([^/]+)$/);
  if (ride && method === "PATCH") {
    const id = decodeURIComponent(ride[1]);
    return writeState(store, (state) => {
      const row = state.rideRequests[id];
      if (!row) return rejected("not found", 404);
      if ("status" in body) row.status = body.status;
      if ("groupId" in body) row.groupId = body.groupId;
      bump(state);
      return saved({ id, ...row });
    });
  }
  if (ride && method === "DELETE") {
    const id = decodeURIComponent(ride[1]);
    return writeState(store, (state) => {
      if (!state.rideRequests[id]) return rejected("not found", 404);
      delete state.rideRequests[id];
      bump(state);
      return saved({ ok: true });
    });
  }

  return json({ error: "Not found." }, 404);
}

export default async function handler(request) {
  const { getStore } = await import("@netlify/blobs");
  const store = getStore({ name: "ess-travel-buddy", consistency: "strong" });
  return handleRequest(request, store);
}
