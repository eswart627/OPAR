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

  static async getMetrics() {
    const res = await fetch("/api/metrics");
    if (!res.ok) throw new Error("Failed fetching metrics");
    return await res.json();
  }
}
