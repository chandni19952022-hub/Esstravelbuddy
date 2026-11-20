/**
 * Live data client for ESS Travel Buddy.
 * Mirrors a simple Firestore-like surface used by the UI.
 */
(function (global) {
  const API_BASE = (global.COMMUTE_API_BASE || "").replace(/\/$/, "");
  const OBJECT_COLLECTIONS = new Set(["presence", "profiles"]);
  const LIST_COLLECTIONS = ["groups", "chat", "feedback", "rideRequests"];

  function createDbClient() {
    let snapshot = {
      presence: {},
      profiles: {},
      groups: [],
      chat: [],
      feedback: [],
      rideRequests: [],
    };
    const listeners = {
      presence: new Set(),
      profiles: new Set(),
      groups: new Set(),
      chat: new Set(),
      feedback: new Set(),
      rideRequests: new Set(),
    };
    let es = null;
    let ready = false;

    function emit(collection) {
      const set = listeners[collection];
      if (!set || !set.size) return;
      const snap = buildSnap(collection);
      set.forEach((cb) => {
        try { cb(snap); } catch (e) { console.error(e); }
      });
    }

    function emitAll() {
      Object.keys(listeners).forEach(emit);
    }

    function buildSnap(collection) {
      if (OBJECT_COLLECTIONS.has(collection)) {
        const obj = snapshot[collection] || {};
        const docs = Object.keys(obj).map((id) => ({
          id,
          exists: true,
          data: () => obj[id],
        }));
        return { docs };
      }
      const list = snapshot[collection] || [];
      const docs = list.map((row) => ({
        id: row.id,
        exists: true,
        data: () => {
          const { id, ...rest } = row;
          return rest;
        },
      }));
      return { docs };
    }

    function applyServerSnapshot(data) {
      snapshot = {
        presence: data.presence || {},
        profiles: data.profiles || {},
        groups: data.groups || [],
        chat: data.chat || [],
        feedback: data.feedback || [],
        rideRequests: data.rideRequests || [],
      };
      emitAll();
    }

    async function refresh() {
      const res = await fetch(API_BASE + "/api/snapshot");
      if (!res.ok) throw new Error("snapshot failed: " + res.status);
      applyServerSnapshot(await res.json());
    }

    function connectSse() {
      if (typeof EventSource === "undefined") return;
      if (es) es.close();
      es = new EventSource(API_BASE + "/api/events");
      es.onmessage = (ev) => {
        try { applyServerSnapshot(JSON.parse(ev.data)); } catch (e) { console.error(e); }
      };
      es.onerror = () => {
        if (es) es.close();
        es = null;
        // Serverless hosts close SSE quickly; reconnect later (polling still covers gaps).
        setTimeout(connectSse, 15000);
      };
    }

    async function request(method, path, body) {
      const opts = { method, headers: { "Content-Type": "application/json" } };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const res = await fetch(API_BASE + path, opts);
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(method + " " + path + " -> " + res.status + " " + text);
      }
      if (res.status === 204) return null;
      return res.json();
    }

    function subscribe(collection, cb) {
      if (!listeners[collection]) listeners[collection] = new Set();
      listeners[collection].add(cb);
      if (ready) {
        try { cb(buildSnap(collection)); } catch (e) { console.error(e); }
      }
      return () => listeners[collection].delete(cb);
    }

    const api = {
      async init() {
        await refresh();
        ready = true;
        connectSse();
        setInterval(() => { refresh().catch(() => {}); }, 8000);
        return api;
      },
      collection(name) {
        return {
          onSnapshot(cb) { return subscribe(name, cb); },
          orderBy() { return this; },
          limit() { return this; },
          async add(data) {
            if (name === "groups") return request("POST", "/api/groups", data);
            if (name === "chat") return request("POST", "/api/chat", data);
            if (name === "feedback") return request("POST", "/api/feedback", data);
            if (name === "rideRequests") return request("POST", "/api/ride-requests", data);
            throw new Error("add not supported for " + name);
          },
        };
      },
      doc(path) {
        const parts = String(path).split("/");
        const collection = parts[0];
        const id = parts.slice(1).join("/");
        return {
          async set(data) {
            if (collection === "presence") return request("PUT", "/api/presence/" + encodeURIComponent(id), data);
            if (collection === "profiles") return request("PUT", "/api/profiles/" + encodeURIComponent(id), data);
            throw new Error("set not supported for " + collection);
          },
          async update(data) {
            if (collection === "groups") return request("PATCH", "/api/groups/" + encodeURIComponent(id), data);
            if (collection === "rideRequests") return request("PATCH", "/api/ride-requests/" + encodeURIComponent(id), data);
            throw new Error("update not supported for " + collection);
          },
          async delete() {
            if (collection === "presence") return request("DELETE", "/api/presence/" + encodeURIComponent(id));
            if (collection === "groups") return request("DELETE", "/api/groups/" + encodeURIComponent(id));
            if (collection === "rideRequests") return request("DELETE", "/api/ride-requests/" + encodeURIComponent(id));
            throw new Error("delete not supported for " + collection);
          },
          async get() {
            if (collection === "groups") {
              const res = await fetch(API_BASE + "/api/groups/" + encodeURIComponent(id));
              if (res.status === 404) return { exists: false, data: () => null };
              if (!res.ok) throw new Error("get failed");
              const row = await res.json();
              return {
                exists: true,
                data: () => {
                  const { id: _id, ...rest } = row;
                  return rest;
                },
              };
            }
            throw new Error("get not supported for " + collection);
          },
        };
      },
    };

    return api;
  }

  global.EssTravelApi = { createDbClient };
})(window);
