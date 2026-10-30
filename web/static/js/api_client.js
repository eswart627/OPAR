// web/static/js/api_client.js
// REST client for Permission-Aware RAG API

export class ApiClient {
  static async getStatus() {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error("Failed fetching system status");
    return await res.json();
  }

  static async runQuery(payload) {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Query failed");
    return data;
  }

  static async uploadDataset(file) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/upload-dataset", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");
    return data;
  }

  static async triggerIngest() {
    const res = await fetch("/api/ingest", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Ingestion failed");
    return data;
  }

  static async getStorageSummary() {
    const res = await fetch("/api/dataset/storage-summary");
    if (!res.ok) throw new Error("Failed fetching storage summary");
    return await res.json();
  }

  static async teardownPipeline() {
    const res = await fetch("/api/dataset/teardown", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Teardown sequence failed");
    return data;
  }

  static async getMetrics() {
    const res = await fetch("/api/metrics");
    if (!res.ok) throw new Error("Failed fetching metrics");
    return await res.json();
  }

  // --- Pipeline Operations & Dynamic Config Endpoints ---

  static async getConfigState() {
    const res = await fetch("/api/config/state");
    if (!res.ok) throw new Error("Failed fetching config state");
    return await res.json();
  }

  static async toggleCache(enabled) {
    const res = await fetch("/api/config/cache", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed updating cache state");
    return data;
  }

  static async invalidateCache(user = null) {
    const res = await fetch("/api/config/cache/invalidate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(user ? { user } : { all: true }),
    });
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`Invalid response from server: ${text.slice(0, 100)}`);
    }
    if (!res.ok) throw new Error(data.error || "Failed invalidating cache");
    return data;
  }

  static async manageDriftQueue(action, provider = "all") {
    const res = await fetch("/api/config/drift-queue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, provider }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed managing drift queue");
    return data;
  }

  static async manageListener(action, provider = "all") {
    const res = await fetch("/api/config/listener", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, provider }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed managing listener daemon");
    return data;
  }

  static async setIdpMode(mode) {
    const res = await fetch("/api/config/idp-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed updating IdP mode");
    return data;
  }

  static async getConfigLogs(sinceId = 0) {
    const url = sinceId ? `/api/config/logs?since_id=${encodeURIComponent(sinceId)}` : "/api/config/logs";
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed fetching drift logs");
    return await res.json();
  }

  static async clearConfigLogs() {
    const res = await fetch("/api/config/logs/clear", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed clearing logs");
    return data;
  }
}
