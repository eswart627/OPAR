// web/static/js/state_store.js
// Reactive centralized state store for Permission-Aware RAG

export class StateStore {
  constructor() {
    this.state = {
      isReady: false,
      dataset: null,
      selectedUser: "user-1",
      selectedMode: "hybrid",
      activeQuestion: "",
      goldAnswer: "",
      goldSupportingFacts: [],
      currentTab: "playground", // 'dataset' | 'playground' | 'benchmark'
      latencies: null,
      lastResult: null,
    };
    this.listeners = [];
  }

  getState() {
    return this.state;
  }

  setState(partial) {
    this.state = { ...this.state, ...partial };
    this.notify();
  }

  subscribe(listener) {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  notify() {
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }
}

export const store = new StateStore();
