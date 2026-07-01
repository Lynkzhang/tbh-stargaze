"""
Steam Market price client for TaskBarHero items.

Design (copied from tbh-copilot's approach):
  - appid: 3678970
  - endpoint: https://steamcommunity.com/market/priceoverview/?appid=3678970&currency=23&market_hash_name=...
  - currency 23 = CNY
  - market_hash_name for gear: "<English Name> (<Grade>) A" first, then "<English Name> (<Grade>)"
  - market_hash_name for material: "<English Name>" (no grade suffix)
  - Only certain rarities are tradeable. Code mirrors tbh-copilot:
        TRADE_GRADES = Legendary, Immortal, Arcana, Beyond
        UNTRADABLE_NOW = Celestial, Divine, Cosmic (not yet enabled by devs)

Cache (mirrors tbh-copilot):
  - TTL = 6 hours (prices move slowly; rate limits are aggressive)
  - On-disk JSON so cache survives process restart
  - Keyed by (currency, market_hash_name) so currency switch never serves wrong data

Rate limiting:
  - 600ms minimum between Steam calls (single-thread sequential queue)
  - On error/timeout, mark "failed" and skip for this cycle, retry next cycle
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

APPID = 3678970
CURRENCY_CNY = 23
PRICE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
FAIL_TTL_SECONDS = 10 * 60  # 10 minutes - don't keep hammering Steam if it's blocked
MIN_CALL_INTERVAL_MS = 600  # delay between Steam calls
HTTP_TIMEOUT_SECONDS = 15

# Per tbh-copilot dashboard.html lines 664-669
TRADE_GRADES = {"LEGENDARY", "IMMORTAL", "ARCANA", "BEYOND"}
UNTRADABLE_NOW = {"CELESTIAL", "DIVINE", "COSMIC"}

# These are the Steam Market grade strings (capitalized).
GRADE_SUFFIX = {
    "LEGENDARY": "Legendary",
    "IMMORTAL": "Immortal",
    "ARCANA": "Arcana",
    "BEYOND": "Beyond",
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


@dataclass
class PriceEntry:
    """One cached price snapshot."""

    price: str | None  # raw display string from Steam, e.g. "¥1.23" or None if no price
    hash_name: str | None  # the hash we actually got a hit on (gear has 2 variants)
    fetched_at: float  # unix time
    failed: bool = False  # if True, network error - retry sooner than TTL

    @property
    def fresh(self) -> bool:
        age = time.time() - self.fetched_at
        if self.failed:
            return age < FAIL_TTL_SECONDS
        return age < PRICE_TTL_SECONDS

    def to_json(self) -> dict:
        return {
            "price": self.price,
            "hash_name": self.hash_name,
            "fetched_at": self.fetched_at,
            "failed": self.failed,
        }

    @classmethod
    def from_json(cls, d: dict) -> "PriceEntry":
        return cls(
            price=d.get("price"),
            hash_name=d.get("hash_name"),
            fetched_at=d.get("fetched_at", 0),
            failed=d.get("failed", False),
        )


class SteamPriceClient:
    """Threadsafe price client with on-disk cache.

    Public API:
        get_cached(market_hash_name) -> PriceEntry | None    (synchronous, never blocks)
        request_async(market_hash_name)                       (queues a fetch, returns immediately)
        steam_link(market_hash_name) -> str                   (listing URL for click-through)
        market_hashes_for_gear(item_id) -> list[str]
        market_hash_for_material(item_id, name) -> str|None
    """

    def __init__(
        self,
        cache_path: Path,
        currency: int = CURRENCY_CNY,
        gear_market_names: dict[str, str] | None = None,
        material_market_names: dict[str, str] | None = None,
        generic_market_names: dict[str, str] | None = None,
        item_grades: dict[str, str] | None = None,
    ) -> None:
        self.cache_path = cache_path
        self.currency = currency
        self.gear_market_names = gear_market_names or {}
        self.material_market_names = material_market_names or {}
        self.generic_market_names = generic_market_names or {}
        self.item_grades = item_grades or {}

        self._cache: dict[str, PriceEntry] = {}
        self._lock = threading.Lock()
        self._queue: list[str] = []  # market_hash_names pending fetch
        self._queue_lock = threading.Lock()
        self._last_call_at = 0.0
        self._worker_started = False

        self._load_cache()

    # ---------------- Cache I/O ----------------

    def _cache_key(self, market_hash_name: str) -> str:
        # Same shape as tbh-copilot: currency-scoped
        return f"{self.currency}|{market_hash_name}"

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if isinstance(v, dict):
                    self._cache[k] = PriceEntry.from_json(v)
        except (OSError, json.JSONDecodeError):
            pass

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({k: v.to_json() for k, v in self._cache.items()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ---------------- Hash building ----------------

    def market_hashes_for_gear(self, item_id: int) -> list[str]:
        """Build possible Steam Market hashes for a gear item."""
        sid = str(item_id)
        name = self.gear_market_names.get(sid)
        if not name:
            return []
        grade = (self.item_grades.get(sid) or "").upper()
        if grade not in TRADE_GRADES:
            return []
        suffix = GRADE_SUFFIX[grade]
        base = f"{name} ({suffix})"
        # tbh-copilot probes the suffixed listing first. Most live gear listings
        # use this " A" variant; the bare name is kept as a fallback.
        return [f"{base} A", base]

    def market_hash_for_gear(self, item_id: int) -> str | None:
        """Return the preferred Steam Market hash for a gear item."""
        hashes = self.market_hashes_for_gear(item_id)
        return hashes[0] if hashes else None

    def market_hash_for_material(self, item_id: int) -> str | None:
        """Materials use just the English name as their Steam hash, no grade suffix."""
        sid = str(item_id)
        name = self.material_market_names.get(sid)
        if not name:
            return None
        return name

    def market_hash_for_generic(self, item_id: int) -> str | None:
        """Generic items (currencies, quest items, soulstones) — English name, no suffix."""
        sid = str(item_id)
        return self.generic_market_names.get(sid)

    def market_hash_for(self, item_id: int) -> str | None:
        """Try gear -> material -> generic in order."""
        return (
            self.market_hash_for_gear(item_id)
            or self.market_hash_for_material(item_id)
            or self.market_hash_for_generic(item_id)
        )

    def market_hashes_for(self, item_id: int) -> list[str]:
        """Try gear -> material -> generic, returning every viable hash variant."""
        gear = self.market_hashes_for_gear(item_id)
        if gear:
            return gear
        material = self.market_hash_for_material(item_id)
        if material:
            return [material]
        generic = self.market_hash_for_generic(item_id)
        if generic:
            return [generic]
        return []

    def steam_link(self, market_hash_name: str) -> str:
        return f"https://steamcommunity.com/market/listings/{APPID}/{urllib.parse.quote(market_hash_name)}"

    # ---------------- Public lookup ----------------

    def get_cached(self, market_hash_name: str) -> PriceEntry | None:
        with self._lock:
            entry = self._cache.get(self._cache_key(market_hash_name))
            if entry and entry.fresh:
                return entry
            return None

    def get_cached_any(self, market_hash_names: list[str]) -> tuple[str, PriceEntry] | None:
        """Return a fresh cached price for any hash, preferring priced entries.

        For gear, the first hash can legitimately be unlisted while the fallback
        hash has a price. Do not settle on a no-price entry until all variants
        have fresh no-price responses. A fresh failed entry is still a completed
        attempt for single-hash items, so callers can show "failed" instead of
        leaving the row pending until the retry TTL expires.
        """
        no_price: list[tuple[str, PriceEntry]] = []
        for hash_name in market_hash_names:
            entry = self.get_cached(hash_name)
            if not entry:
                return None
            if entry.price:
                return hash_name, entry
            no_price.append((hash_name, entry))
        return no_price[0] if no_price else None

    def request_async(self, market_hash_name: str) -> None:
        """Queue a fetch. No-op if already cached & fresh."""
        if self.get_cached(market_hash_name) is not None:
            return
        with self._queue_lock:
            if market_hash_name not in self._queue:
                self._queue.append(market_hash_name)
        self._ensure_worker()

    def request_async_many(self, market_hash_names: list[str]) -> None:
        """Queue every missing hash variant."""
        for hash_name in market_hash_names:
            self.request_async(hash_name)

    # ---------------- Worker thread ----------------

    def _ensure_worker(self) -> None:
        with self._queue_lock:
            if self._worker_started:
                return
            self._worker_started = True
        t = threading.Thread(target=self._worker_loop, name="SteamPriceWorker", daemon=True)
        t.start()

    def _worker_loop(self) -> None:
        while True:
            with self._queue_lock:
                if not self._queue:
                    self._worker_started = False
                    return
                hash_name = self._queue.pop(0)

            # Skip if it became fresh while waiting
            if self.get_cached(hash_name) is not None:
                continue

            # Throttle
            now = time.time()
            elapsed_ms = (now - self._last_call_at) * 1000
            if elapsed_ms < MIN_CALL_INTERVAL_MS:
                time.sleep((MIN_CALL_INTERVAL_MS - elapsed_ms) / 1000)
            self._last_call_at = time.time()

            self._fetch_one(hash_name)

    def _fetch_one(self, market_hash_name: str) -> None:
        """Hit Steam, parse response, save to cache."""
        url = (
            f"https://steamcommunity.com/market/priceoverview/"
            f"?appid={APPID}"
            f"&currency={self.currency}"
            f"&market_hash_name={urllib.parse.quote(market_hash_name)}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            if data.get("success") and data.get("lowest_price"):
                entry = PriceEntry(
                    price=data["lowest_price"],
                    hash_name=market_hash_name,
                    fetched_at=time.time(),
                    failed=False,
                )
            else:
                # Steam responded but no price (item not listed). Cache for full TTL.
                entry = PriceEntry(
                    price=None,
                    hash_name=market_hash_name,
                    fetched_at=time.time(),
                    failed=False,
                )
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
            # Network failure - cache shorter retry
            entry = PriceEntry(
                price=None,
                hash_name=market_hash_name,
                fetched_at=time.time(),
                failed=True,
            )
            _ = e  # silence linter

        with self._lock:
            self._cache[self._cache_key(market_hash_name)] = entry
            self._save_cache()

    # ---------------- Stats (for debugging) ----------------

    def stats(self) -> dict:
        with self._lock:
            n_total = len(self._cache)
            n_priced = sum(1 for e in self._cache.values() if e.price)
            n_failed = sum(1 for e in self._cache.values() if e.failed)
        with self._queue_lock:
            qlen = len(self._queue)
        return {
            "cached_total": n_total,
            "cached_with_price": n_priced,
            "cached_failed": n_failed,
            "queue_pending": qlen,
            "currency": self.currency,
        }
