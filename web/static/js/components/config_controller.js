// web/static/js/components/config_controller.js
// Isolated modular controller for Pipeline Configuration & Live Operations

import { ApiClient } from "../api_client.js";

export class ConfigController {
  constructor() {
    this.pollInterval = null;
    this.lastLogId = 0;
    this.isPollingBusy = false;

    // DOM Elements - Cache
    this.cacheToggle = document.getElementById("cfg-cache-toggle");
    this.cacheBadge = document.getElementById("cfg-cache-badge");
    this.cacheHits = document.getElementById("cfg-cache-hits");
    this.cacheMisses = document.getElementById("cfg-cache-misses");
    this.cacheRatio = document.getElementById("cfg-cache-ratio");
    this.cacheSpeed = document.getElementById("cfg-cache-speed");
    this.btnCacheFlush = document.getElementById("btn-cache-flush");
    this.btnCacheEvictUser = document.getElementById("btn-cache-evict-user");
    this.cacheUserSelect = document.getElementById("cfg-cache-user-select");

    // DOM Elements - IdP
    this.idpAuth0Btn = document.getElementById("cfg-idp-auth0");
    this.idpStaticBtn = document.getElementById("cfg-idp-static");
    this.idpBadge = document.getElementById("cfg-auth-mode-badge");
    this.idpFeedback = document.getElementById("cfg-idp-feedback");

    // DOM Elements - Drift Queues
    this.queuesMasterBadge = document.getElementById("cfg-queues-master-badge");
    this.queuesSpinner = document.getElementById("cfg-queues-spinner");
    this.btnQueuesSetupAll = document.getElementById("btn-queues-setup-all");
    this.btnQueuesTeardownAll = document.getElementById("btn-queues-teardown-all");
    this.awsQueueBadge = document.getElementById("cfg-aws-queue-badge");
    this.gcpQueueBadge = document.getElementById("cfg-gcp-queue-badge");
    this.btnSetupAws = document.getElementById("btn-setup-aws-queue");
    this.btnTeardownAws = document.getElementById("btn-teardown-aws-queue");
    this.btnSetupGcp = document.getElementById("btn-setup-gcp-queue");
    this.btnTeardownGcp = document.getElementById("btn-teardown-gcp-queue");

    // DOM Elements - Listener Daemon & Console
    this.listenerBadge = document.getElementById("cfg-listener-badge");
    this.btnListenerStart = document.getElementById("btn-listener-start");
    this.btnListenerStop = document.getElementById("btn-listener-stop");
    this.listenerProviderSelect = document.getElementById("cfg-listener-provider-select");
    this.terminalConsole = document.getElementById("cfg-terminal-console");
    this.btnClearTerminal = document.getElementById("btn-clear-terminal");
    this.autoscrollChk = document.getElementById("cfg-autoscroll-chk");

    this.bindEvents();
  }

  bindEvents() {
    // 1. Cache Toggle
    if (this.cacheToggle) {
      this.cacheToggle.addEventListener("change", async (e) => {
        const enabled = e.target.checked;
        this.cacheBadge.textContent = enabled ? "Enabling..." : "Disabling...";
        try {
          const res = await ApiClient.toggleCache(enabled);
          this.updateCacheUI(res.cache_state);
        } catch (err) {
          this.appendLogLine(`[CACHE ERROR] Failed to toggle cache: ${err.message}`);
          this.cacheToggle.checked = !enabled;
        }
      });
    }

    // 2. Cache Flush & Evict
    if (this.btnCacheFlush) {
      this.btnCacheFlush.addEventListener("click", async () => {
        try {
          const res = await ApiClient.invalidateCache();
          this.appendLogLine(`[CACHE] Flushed all cached decisions (${res.invalidated_count ?? 0} entries purged).`);
          this.refreshState();
        } catch (err) {
          this.appendLogLine(`[CACHE ERROR] Flush cache failed: ${err.message}`);
        }
      });
    }

    if (this.btnCacheEvictUser && this.cacheUserSelect) {
      this.btnCacheEvictUser.addEventListener("click", async () => {
        const user = this.cacheUserSelect.value;
        try {
          const res = await ApiClient.invalidateCache(user);
          this.appendLogLine(`[CACHE] Evicted cached decisions for persona '${user}' (${res.invalidated_count ?? 0} purged).`);
          this.refreshState();
        } catch (err) {
          this.appendLogLine(`[CACHE ERROR] Evict user failed: ${err.message}`);
        }
      });
    }

    // 3. IdP Mode Switching
    if (this.idpAuth0Btn) {
      this.idpAuth0Btn.addEventListener("click", () => this.switchIdpMode("auth0"));
    }
    if (this.idpStaticBtn) {
      this.idpStaticBtn.addEventListener("click", () => this.switchIdpMode("static"));
    }

    // 4. Multi-Cloud Queue Management
    if (this.btnQueuesSetupAll) {
      this.btnQueuesSetupAll.addEventListener("click", () => this.handleQueueAction("setup", "all"));
    }
    if (this.btnQueuesTeardownAll) {
      this.btnQueuesTeardownAll.addEventListener("click", () => this.handleQueueAction("teardown", "all"));
    }
    if (this.btnSetupAws) {
      this.btnSetupAws.addEventListener("click", () => this.handleQueueAction("setup", "aws"));
    }
    if (this.btnTeardownAws) {
      this.btnTeardownAws.addEventListener("click", () => this.handleQueueAction("teardown", "aws"));
    }
    if (this.btnSetupGcp) {
      this.btnSetupGcp.addEventListener("click", () => this.handleQueueAction("setup", "gcp"));
    }
    if (this.btnTeardownGcp) {
      this.btnTeardownGcp.addEventListener("click", () => this.handleQueueAction("teardown", "gcp"));
    }

    // 5. Listener Daemon Management
    if (this.btnListenerStart) {
      this.btnListenerStart.addEventListener("click", async () => {
        const provider = this.listenerProviderSelect ? this.listenerProviderSelect.value : "all";
        this.btnListenerStart.disabled = true;
        try {
          const res = await ApiClient.manageListener("start", provider);
          const pid = res.listener ? res.listener.pid : res.pid;
          this.appendLogLine(`[DAEMON] Started drift listener daemon (PID: ${pid}) on provider '${provider}'.`);
          this.refreshState();
        } catch (err) {
          this.appendLogLine(`[DAEMON ERROR] Start listener failed: ${err.message}`);
          this.btnListenerStart.disabled = false;
        }
      });
    }

    if (this.btnListenerStop) {
      this.btnListenerStop.addEventListener("click", async () => {
        this.btnListenerStop.disabled = true;
        try {
          await ApiClient.manageListener("stop");
          this.appendLogLine("[DAEMON] Listener daemon process stopped.");
          this.refreshState();
        } catch (err) {
          this.appendLogLine(`[DAEMON ERROR] Stop listener failed: ${err.message}`);
          this.btnListenerStop.disabled = false;
        }
      });
    }

    // 6. Terminal Console Controls
    if (this.btnClearTerminal) {
      this.btnClearTerminal.addEventListener("click", async () => {
        try {
          const res = await ApiClient.clearConfigLogs();
          if (res && res.last_id) {
            this.lastLogId = res.last_id;
          }
          if (this.terminalConsole) {
            this.terminalConsole.innerHTML = '<span class="log-dim">[Console cleared] Ready for events...</span>';
          }
        } catch (err) {
          console.error("Clear logs error", err);
        }
      });
    }
  }

  async switchIdpMode(mode) {
    if (this.idpFeedback) this.idpFeedback.textContent = "Switching identity bridge...";
    try {
      const res = await ApiClient.setIdpMode(mode);
      if (mode === "auth0") {
        this.idpAuth0Btn.classList.add("selected");
        this.idpStaticBtn.classList.remove("selected");
        this.idpBadge.textContent = "Auth0 Enterprise (OIDC + STS)";
        this.idpBadge.className = "badge badge-allowed";
      } else {
        this.idpStaticBtn.classList.add("selected");
        this.idpAuth0Btn.classList.remove("selected");
        this.idpBadge.textContent = "Static IAM (Permanent Keys)";
        this.idpBadge.className = "badge badge-neutral";
      }
      if (this.idpFeedback) {
        this.idpFeedback.textContent = res.message || `Switched to ${mode} mode.`;
        setTimeout(() => { if (this.idpFeedback) this.idpFeedback.textContent = ""; }, 4000);
      }
      this.appendLogLine(`[IdP] Switched active authorization mode to '${mode}'.`);
    } catch (err) {
      this.appendLogLine(`[IdP ERROR] Failed to switch mode: ${err.message}`);
      if (this.idpFeedback) this.idpFeedback.textContent = "";
    }
  }

  async handleQueueAction(action, provider) {
    if (this.queuesSpinner) this.queuesSpinner.style.display = "inline";
    const actLabel = action === "setup" ? "Deploying" : "Destroying";
    this.appendLogLine(`[QUEUES] ${actLabel} drift event queue (${provider})...`);

    try {
      const res = await ApiClient.manageDriftQueue(action, provider);
      this.appendLogLine(`[QUEUES] Action '${action}' on '${provider}' completed successfully.`);
      await this.refreshState();
    } catch (err) {
      this.appendLogLine(`[QUEUES ERROR] ${err.message}`);
    } finally {
      if (this.queuesSpinner) this.queuesSpinner.style.display = "none";
    }
  }

  updateCacheUI(cache) {
    if (!cache) return;
    const isEnabled = !!cache.enabled;
    if (this.cacheToggle && this.cacheToggle.checked !== isEnabled) {
      this.cacheToggle.checked = isEnabled;
    }

    if (this.cacheBadge) {
      this.cacheBadge.className = isEnabled ? "badge badge-allowed" : "badge badge-blocked";
      this.cacheBadge.textContent = isEnabled ? "Enabled" : "Disabled";
    }

    const stats = cache.stats || cache;
    if (this.cacheHits) this.cacheHits.textContent = stats.hits ?? 0;
    if (this.cacheMisses) this.cacheMisses.textContent = stats.misses ?? 0;
    if (this.cacheRatio) {
      let r = 0.0;
      if (stats.hit_ratio_pct !== undefined) {
        r = Number(stats.hit_ratio_pct).toFixed(1);
      } else if (stats.hit_ratio !== undefined) {
        r = (stats.hit_ratio * 100).toFixed(1);
      }
      this.cacheRatio.textContent = `${r}%`;
    }
    if (this.cacheSpeed) {
      const sp = stats.avg_cached_ms ?? stats.speed_ms ?? 0.05;
      this.cacheSpeed.textContent = `${sp} ms`;
    }
  }

  updateQueuesUI(queues) {
    if (!queues) return;

    // AWS badge
    const awsDeployed = queues.aws ? (queues.aws.deployed !== undefined ? queues.aws.deployed : queues.aws.status === "deployed") : false;
    if (this.awsQueueBadge) {
      if (awsDeployed) {
        this.awsQueueBadge.className = "badge badge-allowed";
        this.awsQueueBadge.textContent = "Deployed & Bound";
      } else {
        this.awsQueueBadge.className = "badge badge-blocked";
        this.awsQueueBadge.textContent = "Not Deployed";
      }
    }

    // GCP badge
    const gcpDeployed = queues.gcp ? (queues.gcp.deployed !== undefined ? queues.gcp.deployed : queues.gcp.status === "deployed") : false;
    if (this.gcpQueueBadge) {
      if (gcpDeployed) {
        this.gcpQueueBadge.className = "badge badge-allowed";
        this.gcpQueueBadge.textContent = "Deployed & Bound";
      } else {
        this.gcpQueueBadge.className = "badge badge-blocked";
        this.gcpQueueBadge.textContent = "Not Deployed";
      }
    }

    // Master badge
    if (this.queuesMasterBadge) {
      if (awsDeployed && gcpDeployed) {
        this.queuesMasterBadge.className = "badge badge-allowed";
        this.queuesMasterBadge.textContent = "Multi-Cloud Active (AWS + GCP)";
      } else if (awsDeployed || gcpDeployed) {
        this.queuesMasterBadge.className = "badge badge-supporting";
        this.queuesMasterBadge.textContent = "Partially Deployed";
      } else {
        this.queuesMasterBadge.className = "badge badge-blocked";
        this.queuesMasterBadge.textContent = "Not Deployed";
      }
    }
  }

  updateListenerUI(listener) {
    if (!listener) return;
    const isRunning = !!listener.running;

    if (this.listenerBadge) {
      this.listenerBadge.className = isRunning ? "badge badge-allowed" : "badge badge-blocked";
      this.listenerBadge.textContent = isRunning ? `Running (PID: ${listener.pid})` : "Stopped";
    }

    if (this.btnListenerStart) this.btnListenerStart.disabled = isRunning;
    if (this.btnListenerStop) this.btnListenerStop.disabled = !isRunning;
  }

  appendLogLine(rawText) {
    if (!this.terminalConsole) return;

    // Check if initial placeholder exists
    if (this.terminalConsole.querySelector(".log-dim") && this.terminalConsole.children.length === 1) {
      this.terminalConsole.innerHTML = "";
    }

    const line = document.createElement("div");
    let colorClass = "log-dim";

    if (rawText.includes("[AWS SQS]") || rawText.includes("AWS")) {
      colorClass = "log-aws";
    } else if (rawText.includes("[GCP PubSub]") || rawText.includes("GCP")) {
      colorClass = "log-gcp";
    } else if (rawText.includes("metadata.db updated") || rawText.includes("[DAEMON]")) {
      colorClass = "log-daemon";
    } else if (rawText.toLowerCase().includes("error") || rawText.toLowerCase().includes("failed")) {
      colorClass = "log-err";
    }

    line.className = colorClass;
    line.textContent = rawText;
    this.terminalConsole.appendChild(line);

    // Limit visible lines to prevent DOM bloat
    while (this.terminalConsole.children.length > 300) {
      this.terminalConsole.removeChild(this.terminalConsole.firstChild);
    }

    // Autoscroll
    if (this.autoscrollChk && this.autoscrollChk.checked) {
      this.terminalConsole.scrollTop = this.terminalConsole.scrollHeight;
    }
  }

  async fetchLogs() {
    try {
      const data = await ApiClient.getConfigLogs(this.lastLogId);
      if (data && data.logs && data.logs.length > 0) {
        data.logs.forEach((entry) => {
          this.appendLogLine(entry.raw || `[${entry.source}] ${entry.message}`);
          if (entry.id && entry.id > this.lastLogId) {
            this.lastLogId = entry.id;
          }
        });
      }
      if (data && data.last_id && data.last_id > this.lastLogId) {
        this.lastLogId = data.last_id;
      }
    } catch (err) {
      // Background poll silently logs
      console.warn("Poll drift logs error:", err.message);
    }
  }

  async refreshState() {
    try {
      const state = await ApiClient.getConfigState();
      this.updateCacheUI(state.cache);
      this.updateQueuesUI(state.queues);
      this.updateListenerUI(state.listener);

      const authMode = state.auth_mode || (state.idp ? state.idp.mode : "auth0");
      if (this.idpBadge) {
        if (authMode === "auth0") {
          this.idpAuth0Btn.classList.add("selected");
          this.idpStaticBtn.classList.remove("selected");
          this.idpBadge.textContent = "Auth0 Enterprise (OIDC + STS)";
          this.idpBadge.className = "badge badge-allowed";
        } else {
          this.idpStaticBtn.classList.add("selected");
          this.idpAuth0Btn.classList.remove("selected");
          this.idpBadge.textContent = "Static IAM (Permanent Keys)";
          this.idpBadge.className = "badge badge-neutral";
        }
      }
    } catch (err) {
      console.warn("Config state poll failed:", err.message);
    }
  }

  startPolling(intervalMs = 1500) {
    if (this.pollInterval) clearInterval(this.pollInterval);
    // Initial fetch
    this.refreshState();
    this.fetchLogs();

    this.pollInterval = setInterval(async () => {
      if (this.isPollingBusy) return;
      this.isPollingBusy = true;
      try {
        await Promise.all([this.refreshState(), this.fetchLogs()]);
      } finally {
        this.isPollingBusy = false;
      }
    }, intervalMs);
  }

  stopPolling() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }
}

// Helper to update the badge in Query & Audit tab
export function renderQueryCacheIndicator(cacheVerdict) {
  const indicator = document.getElementById("query-cache-indicator");
  if (!indicator) return;

  if (!cacheVerdict || !cacheVerdict.enabled) {
    indicator.style.display = "none";
    return;
  }

  indicator.style.display = "inline-flex";
  if (cacheVerdict.hit) {
    indicator.className = "badge badge-allowed";
    indicator.textContent = `⚡ Cache: HIT (${cacheVerdict.latency_ms} ms)`;
  } else {
    indicator.className = "badge badge-neutral";
    indicator.textContent = `⚡ Cache: MISS (${cacheVerdict.latency_ms} ms live)`;
  }
}
