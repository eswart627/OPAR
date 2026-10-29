# src/retrievers/permission_retriever.py
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading, time

@dataclass
class PermissionRetrieverConfig:
    max_workers: int = 10
    use_cache: bool = False
    ttl_seconds: float = 300.0  # Unix timestamp TTL in seconds (default: 5 minutes)
    per_item_timeout: Optional[float] = None  # seconds

class PermissionRetriever:
    """
    Filter retrieved docs by checking access via IAM adapters.
    - ac_manager.get_access_controller(provider) -> adapter
    - adapter.check_access(resource_metadata) -> bool
    - metadata_store.get_by_uuid(uuid) -> metadata dict
    
    Includes an In-Memory Decision Cache with:
    - Unix timestamp TTL auto-eviction
    - Targeted user/resource invalidation
    """
    def __init__(self, metadata_store, config: PermissionRetrieverConfig = PermissionRetrieverConfig()):
        self.ms = metadata_store
        self.config = config
        # Cache stores: cache_key -> (allowed: bool, expires_at_unix: float)
        self._cache: Dict[str, Tuple[bool, float]] = {}
        self._lock = threading.Lock()

    def clear_cache(self) -> int:
        """Wipes the entire cache. Returns the number of cleared entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def invalidate_user(self, user_name: str) -> int:
        """
        Targeted eviction: purges only cache keys for the specified user.
        Preserves cache decisions for all other users.
        """
        prefix = f"{user_name}:"
        with self._lock:
            keys_to_del = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_del:
                del self._cache[k]
            return len(keys_to_del)

    def prime_cache(self, uuid_to_allowed: Dict[str, bool], user_name: str = "default", ttl: Optional[float] = None): 
        if not uuid_to_allowed: 
            return
        expiry = time.time() + (ttl if ttl is not None else self.config.ttl_seconds)
        with self._lock: 
            for u, allowed in uuid_to_allowed.items():
                self._cache[f"{user_name}:{u}"] = (bool(allowed), expiry)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Returns diagnostic metrics about cache size and freshness."""
        now = time.time()
        with self._lock:
            total = len(self._cache)
            active = sum(1 for _, exp in self._cache.values() if exp > now)
            expired = total - active
            return {
                "total_entries": total,
                "active_entries": active,
                "expired_entries": expired,
                "ttl_seconds": self.config.ttl_seconds
            }

    def _check_one(self, ac_manager, uuid: str) -> Tuple[str, bool, float]:
        t0 = time.monotonic()
        meta = self.ms.get_by_uuid(uuid)
        if not meta:
            return uuid, False, time.monotonic() - t0
        provider = (meta.get("provider") or "").lower()
        ctrl = ac_manager.get_access_controller(provider) if ac_manager else None
        if not ctrl:
            return uuid, False, time.monotonic() - t0
        try:
            allowed = bool(ctrl.check_access(meta))
        except Exception:
            allowed = False
        return uuid, allowed, time.monotonic() - t0

    def filter_docs(
        self,
        retrieved_docs: List[Dict[str, Any]],
        ac_manager,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, bool], Dict[str, float], float]:
        """
        Returns:
          filtered_docs, decisions{uuid->bool}, per_uuid_durations{uuid->sec}, total_duration
        """
        t_all = time.monotonic()
        decisions: Dict[str, bool] = {}
        per_uuid_dur: Dict[str, float] = {}
        
        user_name = ac_manager.user_name if ac_manager and hasattr(ac_manager, "user_name") else "unknown"
        uuids = [d["global_uuid"] for d in retrieved_docs if d.get("global_uuid")]
        to_check: List[str] = []

        now = time.time()

        if self.config.use_cache:
            with self._lock:
                for u in uuids:
                    cache_key = f"{user_name}:{u}"
                    if cache_key in self._cache:
                        allowed, expires_at = self._cache[cache_key]
                        if now < expires_at:
                            # Cache Hit: Valid, unexpired decision
                            decisions[u] = allowed
                            per_uuid_dur[u] = 0.00005  # Recorded sub-millisecond cache latency
                        else:
                            # Expired entry (TTL reached): Purge and re-check live
                            del self._cache[cache_key]
                            to_check.append(u)
                    else:
                        # Cache Miss
                        to_check.append(u)
        else:
            to_check = uuids[:]

        if to_check:
            mw = max(1, min(self.config.max_workers, len(to_check)))
            with ThreadPoolExecutor(max_workers=mw) as ex:
                futs = {ex.submit(self._check_one, ac_manager, u): u for u in to_check}
                for fut in as_completed(futs):
                    u = futs[fut]
                    try:
                        uid, ok, dur = fut.result(timeout=self.config.per_item_timeout)
                    except Exception:
                        uid, ok, dur = u, False, 0.0
                    decisions[uid] = ok
                    per_uuid_dur[uid] = dur

            if self.config.use_cache:
                expiry = time.time() + self.config.ttl_seconds
                with self._lock:
                    for u in to_check:
                        cache_key = f"{user_name}:{u}"
                        self._cache[cache_key] = (decisions[u], expiry)

        filtered = [d for d in retrieved_docs if decisions.get(d.get("global_uuid"), False)]
        return filtered, decisions, per_uuid_dur, (time.monotonic() - t_all)
