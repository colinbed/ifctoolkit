(function initIfcSessionShared(global) {
  const STORAGE_KEY = "ifc_toolkit_session_id";
  const LEGACY_STORAGE_KEYS = ["ifcToolkitSessionId"];
  const SESSION_CHANGE_EVENT = "ifc-toolkit-session-changed";
  let currentSessionId = "";
  let sessionPromise = null;
  const listeners = new Set();

  function readStoredSessionId() {
    try {
      const canonicalId = sessionStorage.getItem(STORAGE_KEY) || localStorage.getItem(STORAGE_KEY) || "";
      const normalizedCanonical = String(canonicalId || "").trim();
      // Migrate deployments that persisted an ephemeral processing id indefinitely.
      localStorage.removeItem(STORAGE_KEY);
      if (normalizedCanonical) sessionStorage.setItem(STORAGE_KEY, normalizedCanonical);
      if (normalizedCanonical) return normalizedCanonical;
      return "";
    } catch (_) {
      return "";
    }
  }

  function writeStoredSessionId(sessionId) {
    const value = String(sessionId || "").trim();
    try {
      if (value) {
        sessionStorage.setItem(STORAGE_KEY, value);
      } else {
        sessionStorage.removeItem(STORAGE_KEY);
      }
      localStorage.removeItem(STORAGE_KEY);
    } catch (_) {
      // no-op for private mode/storage-disabled environments
    }
  }

  function notifySessionChange(sessionId) {
    try {
      global.dispatchEvent(new CustomEvent(SESSION_CHANGE_EVENT, { detail: { sessionId } }));
    } catch (_) {
      // no-op if CustomEvent is unavailable
    }
    listeners.forEach((listener) => {
      try {
        listener(sessionId);
      } catch (err) {
        console.warn("Session listener failed", err);
      }
    });
  }

  function setCurrentSessionId(sessionId) {
    const normalized = String(sessionId || "").trim();
    if (normalized === currentSessionId) return currentSessionId;
    currentSessionId = normalized;
    writeStoredSessionId(normalized);
    notifySessionChange(normalized);
    return currentSessionId;
  }

  function getCurrentSessionId() {
    if (currentSessionId) return currentSessionId;
    currentSessionId = readStoredSessionId();
    return currentSessionId;
  }

  function getActiveSessionId() {
    return getCurrentSessionId();
  }

  async function ensureSession(options = {}) {
    const { createIfMissing = true, validate = true, forceNew = false } = options;
    const existing = getCurrentSessionId();
    if (existing && !validate && !forceNew) return existing;
    if (!existing && !createIfMissing) return existing;
    if (!sessionPromise) {
      sessionPromise = fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: forceNew ? "" : (existing || "") }),
      })
        .then(async (resp) => {
          if (!resp.ok) throw new Error(`Session request failed (${resp.status})`);
          const data = await resp.json();
          const resolved = String(data?.session_id || "").trim();
          if (!resolved) throw new Error("Session response missing session_id");
          setCurrentSessionId(resolved);
          return resolved;
        })
        .finally(() => {
          sessionPromise = null;
        });
    }
    return sessionPromise;
  }

  async function recoverSession(staleSessionId = "") {
    const stale = String(staleSessionId || "").trim();
    if (stale && stale !== getCurrentSessionId()) setCurrentSessionId(stale);
    return ensureSession({ createIfMissing: true, validate: true });
  }

  function isSessionNotFound(status, body) {
    const detail = body?.detail || body || {};
    return (status === 404 || status === 410) && (detail?.code === "SESSION_NOT_FOUND" || detail?.error === "SESSION_NOT_FOUND");
  }

  function shortSessionId(sessionId, len = 8) {
    const value = String(sessionId || "").trim();
    return value ? value.slice(0, Math.max(1, len)) : "";
  }

  function normalizeSessionFile(record = {}) {
    const candidates = [record.name, record.filename, record.display_name, record.path]
      .map((value) => String(value || "").trim())
      .filter(Boolean);
    const rawName = candidates[0] || "";
    const basename = rawName.split(/[\\/]/).pop() || rawName;
    const size = Number(record.size ?? record.bytes ?? 0) || 0;
    return {
      ...record,
      name: basename,
      size,
      modified: record.modified || record.uploaded || record.uploaded_at || "",
    };
  }

  function inferSessionResponseShape(data) {
    if (Array.isArray(data)) return "array";
    if (data && typeof data === "object") {
      if (Array.isArray(data.files)) return "object.files";
      if (Array.isArray(data.items)) return "object.items";
      return "object.unknown";
    }
    return typeof data;
  }

  async function getSessionFiles(sessionId, options = {}) {
    const sid = String(sessionId || "").trim();
    if (!sid) return [];
    const url = `/api/session/${sid}/files`;
    const resp = await fetch(url);
    let data = null;
    let bodyText = "";
    try {
      data = await resp.json();
    } catch (_) {
      try {
        bodyText = await resp.text();
      } catch (_) {
        bodyText = "";
      }
    }
    if (typeof options?.onResponse === "function") {
      try {
        options.onResponse({
          status: resp.status,
          ok: resp.ok,
          url,
          shape: inferSessionResponseShape(data),
          payload: data ?? bodyText,
        });
      } catch (_) {
        // no-op: diagnostic hooks should never break fetch flow
      }
    }
    if (!resp.ok && !options.retried && isSessionNotFound(resp.status, data)) {
      const replacement = await recoverSession(sid);
      return getSessionFiles(replacement, { ...options, retried: true });
    }
    if (!resp.ok) {
      const message = `Failed to list session files (HTTP ${resp.status})`;
      const err = new Error(message);
      err.status = resp.status;
      err.body = data ?? bodyText;
      throw err;
    }
    const records = Array.isArray(data)
      ? data
      : Array.isArray(data?.files)
        ? data.files
        : Array.isArray(data?.items)
          ? data.items
          : [];
    return records.map((record) => normalizeSessionFile(record));
  }

  function isIfcCandidate(file) {
    const name = String(file?.name || file?.filename || file?.display_name || file?.path || "").toLowerCase();
    return name.endsWith(".ifc") || name.endsWith(".ifczip") || name.endsWith(".ifcxml");
  }

  function subscribe(listener) {
    if (typeof listener !== "function") return () => {};
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  global.IFCSession = {
    storageKey: STORAGE_KEY,
    legacyStorageKeys: LEGACY_STORAGE_KEYS.slice(),
    sessionChangeEvent: SESSION_CHANGE_EVENT,
    getCurrentSessionId,
    getActiveSessionId,
    setCurrentSessionId,
    ensureSession,
    recoverSession,
    isSessionNotFound,
    shortSessionId,
    normalizeSessionFile,
    getSessionFiles,
    isIfcCandidate,
    subscribe,
  };
})(window);
