"""
TBH Box Queue Reader - Minimal Python Shell

Drops the original tool's accelerator-software dependency by skipping
the mutex check. Reuses the original Frida agent (drop_items_agent.js)
verbatim for memory reading logic.

Two run modes:
  - cli   : print queue updates to stdout as JSON lines
  - http  : start an HTTP + WebSocket server on 127.0.0.1:PORT
            HTTP  GET /queue       -> latest queue JSON
            HTTP  GET /watched     -> watched ids list
            WS    /ws              -> push every queue update
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

# Make sibling modules importable when launched from any cwd. The portable
# Python embeddable doesn't add the script's directory to sys.path the same
# way a normal Python install does, so we do it explicitly.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# Imported eagerly so missing modules fail at startup, not 10 lines into http_mode.
from steam_price import SteamPriceClient  # noqa: E402

# Force UTF-8 stdout on Windows so Chinese characters print correctly in PowerShell.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------------------
# Single-instance lock. Frida attaching twice to the same target process can
# crash the game. We bind a UDP socket to a fixed loopback port as the lock -
# OS guarantees only one process can hold it. The socket auto-releases on exit.
# ---------------------------------------------------------------------------
_LOCK_PORT = 18764  # one less than default HTTP port; never used by anything
_lock_socket: socket.socket | None = None


def acquire_single_instance_lock() -> bool:
    global _lock_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", _LOCK_PORT))
    except OSError:
        s.close()
        return False
    _lock_socket = s
    atexit.register(_release_lock)
    return True


def _release_lock() -> None:
    global _lock_socket
    if _lock_socket is not None:
        try:
            _lock_socket.close()
        except OSError:
            pass
        _lock_socket = None

try:
    import frida
except ImportError:
    print("ERROR: frida not installed. Run: python -m pip install frida", file=sys.stderr)
    sys.exit(1)

import psutil

ROOT = Path(__file__).resolve().parent
RES_DIR = ROOT / "resources"
PROCESS_NAME = "TaskBarHero.exe"
AGENT_FILE = RES_DIR / "drop_items_agent.js"
ITEM_NAMES_FILE = RES_DIR / "item.json"
ITEM_GRADES_FILE = RES_DIR / "item_grades.json"
GEAR_MARKET_NAMES_FILE = RES_DIR / "gear_market_names.json"
MATERIAL_MARKET_NAMES_FILE = RES_DIR / "material_market_names.json"
GENERIC_MARKET_NAMES_FILE = RES_DIR / "generic_market_names.json"
COLOR_FILE = RES_DIR / "item_color.json"
WATCHED_FILE = RES_DIR / "watched_ids.json"
PRICE_CACHE_FILE = RES_DIR / "steam_price_cache.json"


def find_pid(name: str) -> int | None:
    name_lower = name.lower()
    for p in psutil.process_iter(["name"]):
        pname = (p.info.get("name") or "").lower()
        if pname == name_lower:
            return p.pid
    return None


def load_json_safe(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class QueueState:
    """Holds the latest queue snapshot. Thread-safe for read/write."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.normal: list[int] = []
        self.boss: list[int] = []
        self.act: list[int] = []
        self.last_update: float = 0.0
        self.connected: bool = False
        self.status_msg: str = "init"

    def set_queue(self, normal: list[int], boss: list[int], act: list[int]) -> None:
        with self._lock:
            self.normal = list(normal)
            self.boss = list(boss)
            self.act = list(act)
            self.last_update = time.time()
            self.connected = True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "status": self.status_msg,
                "last_update": self.last_update,
                "normal": list(self.normal),
                "boss": list(self.boss),
                "act": list(self.act),
            }

    def set_status(self, msg: str) -> None:
        with self._lock:
            self.status_msg = msg


class FridaWorker:
    """Manages Frida session lifecycle for a single TaskBarHero process."""

    def __init__(
        self,
        state: QueueState,
        on_msg=None,
        item_names: dict | None = None,
        watched_ids: set[int] | None = None,
        watched_callback=None,
    ) -> None:
        self.state = state
        self.on_msg = on_msg
        self.item_names = item_names or {}
        self.watched_ids = watched_ids or set()
        self.watched_callback = watched_callback
        self._stop = threading.Event()
        self._session = None
        self._script = None

    def stop(self) -> None:
        """Tear down agent and session in the correct order.

        Order matters: must unload the script first (which lets all its
        setInterval callbacks finish their current tick) BEFORE detaching
        the session. Detaching while Memory.scan is still mid-flight can
        corrupt the target process state.
        """
        self._stop.set()
        try:
            if self._script is not None:
                self._script.unload()
                # Give the script's setInterval / Memory.scan one tick to finish
                time.sleep(0.5)
        except Exception:  # noqa: BLE001 - frida raises generic exceptions
            pass
        self._script = None
        try:
            if self._session is not None:
                self._session.detach()
        except Exception:  # noqa: BLE001
            pass
        self._session = None

    def run(self) -> None:
        while not self._stop.is_set():
            pid = find_pid(PROCESS_NAME)
            if pid is None:
                self.state.set_status(f"等待 {PROCESS_NAME} 启动…")
                time.sleep(2)
                continue
            self.state.set_status(f"已找到 {PROCESS_NAME} (pid={pid})，正在注入…")
            try:
                self._session = frida.attach(pid)
                agent_src = AGENT_FILE.read_text(encoding="utf-8")
                self._script = self._session.create_script(agent_src)
                self._script.on("message", self._on_message)
                self._script.load()
                self.state.set_status("Agent 已加载，等待掉落队列…")
                while not self._stop.is_set():
                    time.sleep(0.5)
                    if find_pid(PROCESS_NAME) is None:
                        self.state.set_status("游戏进程已退出，等待重启…")
                        self.state.connected = False
                        break
            except frida.ProcessNotFoundError:
                self.state.set_status("进程消失，重新搜索…")
            except frida.TransportError as e:
                self.state.set_status(f"Frida 传输错误: {e}")
            except Exception as e:  # noqa: BLE001 - want to log any unexpected exception
                self.state.set_status(f"注入失败: {type(e).__name__}: {e}")
            finally:
                self.stop_session_only()
            time.sleep(2)

    def stop_session_only(self) -> None:
        try:
            if self._script is not None:
                self._script.unload()
        except Exception:  # noqa: BLE001
            pass
        self._script = None
        try:
            if self._session is not None:
                self._session.detach()
        except Exception:  # noqa: BLE001
            pass
        self._session = None

    def _on_message(self, message: dict, data) -> None:
        if message.get("type") != "send":
            err = message.get("description") or message.get("stack") or str(message)
            self.state.set_status(f"Frida 错误: {err}")
            return
        payload = message.get("payload")
        if not isinstance(payload, str):
            return
        try:
            msg = json.loads(payload)
        except json.JSONDecodeError:
            return
        mtype = msg.get("type")
        if mtype == "queue":
            normal = msg.get("normal") or []
            boss = msg.get("boss") or []
            act = msg.get("act") or []
            self.state.set_queue(normal, boss, act)
            if self.watched_callback is not None and self.watched_ids:
                hits = [
                    iid
                    for iid in (normal + boss)
                    if iid in self.watched_ids
                ]
                if hits:
                    self.watched_callback(hits)
        elif mtype == "diag":
            self.state.set_status(msg.get("msg") or "diag")
        elif mtype == "error":
            self.state.set_status(f"Agent 错误: {msg.get('msg')}")
        elif mtype == "ready":
            self.state.set_status("Agent ready")
        elif mtype == "box_open":
            count = msg.get("count")
            self.state.set_status(f"已开启 {count} 个箱子")
        if self.on_msg is not None:
            self.on_msg(msg)


def cli_mode() -> int:
    state = QueueState()
    item_names = load_json_safe(ITEM_NAMES_FILE, {}).get("item", {})
    watched_cfg = load_json_safe(WATCHED_FILE, {})
    watched_ids = set(watched_cfg.get("watched_ids", []))

    def on_msg(msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "queue":
            normal = msg.get("normal") or []
            boss = msg.get("boss") or []
            named = [
                f"{iid}({item_names.get(str(iid), '?')})"
                for iid in normal[:10]
            ]
            print(f"[{time.strftime('%H:%M:%S')}] 普通队列前10: {', '.join(named)}", flush=True)
            if boss:
                bnamed = [
                    f"{iid}({item_names.get(str(iid), '?')})"
                    for iid in boss[:5]
                ]
                print(f"             首领队列前5: {', '.join(bnamed)}", flush=True)
        elif mtype in {"diag", "error", "ready"}:
            print(f"[{mtype}] {msg.get('msg', '')}", flush=True)
        elif mtype == "box_open":
            print(f"[box_open] count={msg.get('count')}", flush=True)

    def on_watched(hits: list[int]) -> None:
        names = [item_names.get(str(i), "?") for i in hits]
        print(f"\a*** 关注物品命中: {list(zip(hits, names))} ***", flush=True)

    worker = FridaWorker(
        state,
        on_msg=on_msg,
        item_names=item_names,
        watched_ids=watched_ids,
        watched_callback=on_watched,
    )
    print(f"Watched IDs: {sorted(watched_ids)}", flush=True)
    print(f"Item dict size: {len(item_names)}", flush=True)
    try:
        worker.run()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        worker.stop()
    return 0


def http_mode(host: str, port: int) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = QueueState()
    item_names = load_json_safe(ITEM_NAMES_FILE, {}).get("item", {})
    item_grades = load_json_safe(ITEM_GRADES_FILE, {})
    gear_market_names = load_json_safe(GEAR_MARKET_NAMES_FILE, {})
    material_market_names = load_json_safe(MATERIAL_MARKET_NAMES_FILE, {})
    generic_market_names = load_json_safe(GENERIC_MARKET_NAMES_FILE, {})
    color_cfg = load_json_safe(COLOR_FILE, {})
    watched_cfg = load_json_safe(WATCHED_FILE, {})

    price_client = SteamPriceClient(
        cache_path=PRICE_CACHE_FILE,
        gear_market_names=gear_market_names,
        material_market_names=material_market_names,
        generic_market_names=generic_market_names,
        item_grades=item_grades,
    )

    # Mutable so we can hot-update from HTTP. Sets aren't JSON-serializable
    # directly but we serialise via sorted list at the boundary.
    watched_lock = threading.Lock()
    watched_ids: set[int] = set(watched_cfg.get("watched_ids", []))

    def persist_watched() -> None:
        """Write current watched_ids back to disk so it survives restart."""
        payload = {"version": 1, "watched_ids": sorted(watched_ids)}
        WATCHED_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    worker = FridaWorker(
        state,
        item_names=item_names,
        watched_ids=watched_ids,  # shared reference - mutations seen by worker
    )
    threading.Thread(target=worker.run, daemon=True).start()

    web_dir = ROOT / "web"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self, rel: str, content_type: str) -> None:
            path = web_dir / rel
            if not path.exists():
                self._json({"error": "not found"}, status=404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1 << 20:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        # ---------- GET ----------
        def do_GET(self):  # noqa: N802
            p = self.path.split("?", 1)[0]
            if p == "/" or p == "/index.html":
                self._static("index.html", "text/html; charset=utf-8")
            elif p == "/app.js":
                self._static("app.js", "application/javascript; charset=utf-8")
            elif p == "/app.css":
                self._static("app.css", "text/css; charset=utf-8")
            elif p == "/queue":
                snap = state.snapshot()
                # Drop the raw 'act' bucket from the response; we only render
                # normal + boss in the web UI.
                snap.pop("act", None)

                def enrich(iid: int) -> dict:
                    """Attach name, grade, and (if available) price info."""
                    out = {
                        "id": iid,
                        "name": item_names.get(str(iid), "?"),
                        "grade": item_grades.get(str(iid), ""),
                    }
                    hash_names = price_client.market_hashes_for(iid)
                    if hash_names:
                        out["market_hash"] = hash_names[0]
                        out["market_link"] = price_client.steam_link(hash_names[0])
                        cached = price_client.get_cached_any(hash_names)
                        if cached is not None:
                            hash_name, entry = cached
                            out["market_hash"] = hash_name
                            out["market_link"] = price_client.steam_link(hash_name)
                            out["price"] = entry.price  # None means tried but no listing
                            out["price_failed"] = entry.failed
                        else:
                            # Not cached - queue a fetch for next poll, mark pending now.
                            price_client.request_async_many(hash_names)
                            out["price_pending"] = True
                    else:
                        # Not tradeable at all (wrong grade or unknown name)
                        out["price_unavailable"] = True
                    return out

                snap["normal_named"] = [enrich(i) for i in snap["normal"]]
                snap["boss_named"] = [enrich(i) for i in snap["boss"]]
                with watched_lock:
                    snap["watched_ids"] = sorted(watched_ids)
                self._json(snap)
            elif p == "/watched":
                with watched_lock:
                    self._json({"watched_ids": sorted(watched_ids)})
            elif p == "/colors":
                self._json(color_cfg)
            elif p == "/items":
                # Full id->name dict for client-side search box autocompletion
                self._json({"item": item_names})
            elif p == "/grades":
                # Full id->GRADE dict so client can colour search results too
                self._json(item_grades)
            elif p.startswith("/price/"):
                tail = p[len("/price/"):]
                if tail == "stats":
                    self._json(price_client.stats())
                else:
                    try:
                        iid = int(tail)
                    except ValueError:
                        self._json({"error": "bad id"}, status=400)
                        return
                    hash_names = price_client.market_hashes_for(iid)
                    if not hash_names:
                        self._json({
                            "id": iid,
                            "price_unavailable": True,
                            "reason": "not tradeable (wrong grade or unknown item)",
                        })
                        return
                    cached = price_client.get_cached_any(hash_names)
                    if cached is not None:
                        hash_name, entry = cached
                        self._json({
                            "id": iid,
                            "market_hash": hash_name,
                            "market_link": price_client.steam_link(hash_name),
                            "price": entry.price,
                            "fetched_at": entry.fetched_at,
                            "failed": entry.failed,
                        })
                    else:
                        price_client.request_async_many(hash_names)
                        self._json({
                            "id": iid,
                            "market_hash": hash_names[0],
                            "market_link": price_client.steam_link(hash_names[0]),
                            "price_pending": True,
                        })
            elif p == "/health":
                self._json({"ok": True, "connected": state.connected, "status": state.status_msg})
            else:
                self._json({"error": "not found"}, status=404)

        # ---------- POST ----------
        def do_POST(self):  # noqa: N802
            p = self.path.split("?", 1)[0]
            if p == "/watched/add":
                body = self._read_body()
                ids = body.get("ids") or []
                added = []
                with watched_lock:
                    for raw in ids:
                        try:
                            iid = int(raw)
                        except (TypeError, ValueError):
                            continue
                        if iid not in watched_ids:
                            watched_ids.add(iid)
                            added.append(iid)
                    try:
                        persist_watched()
                    except OSError as e:
                        self._json({"ok": False, "error": str(e)}, status=500)
                        return
                    self._json({"ok": True, "added": added, "watched_ids": sorted(watched_ids)})
            elif p == "/watched/remove":
                body = self._read_body()
                ids = body.get("ids") or []
                removed = []
                with watched_lock:
                    for raw in ids:
                        try:
                            iid = int(raw)
                        except (TypeError, ValueError):
                            continue
                        if iid in watched_ids:
                            watched_ids.discard(iid)
                            removed.append(iid)
                    try:
                        persist_watched()
                    except OSError as e:
                        self._json({"ok": False, "error": str(e)}, status=500)
                        return
                    self._json({"ok": True, "removed": removed, "watched_ids": sorted(watched_ids)})
            elif p == "/watched/reload":
                # Re-read JSON file from disk (in case user edited it manually).
                fresh = load_json_safe(WATCHED_FILE, {}).get("watched_ids", [])
                with watched_lock:
                    watched_ids.clear()
                    watched_ids.update(int(x) for x in fresh)
                    self._json({"ok": True, "watched_ids": sorted(watched_ids)})
            else:
                self._json({"error": "not found"}, status=404)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"=== TBH Box Queue Reader ===", flush=True)
    print(f"Web UI:  http://{host}:{port}/", flush=True)
    print(f"API:", flush=True)
    print(f"  GET  /queue           - latest queue snapshot (with prices)", flush=True)
    print(f"  GET  /watched         - watched IDs", flush=True)
    print(f"  GET  /colors          - color config", flush=True)
    print(f"  GET  /items           - full item dict", flush=True)
    print(f"  GET  /grades          - id -> rarity grade", flush=True)
    print(f"  GET  /price/<id>      - latest cached price for item", flush=True)
    print(f"  GET  /price/stats     - price cache stats", flush=True)
    print(f"  GET  /health          - status", flush=True)
    print(f"  POST /watched/add     {{ids:[...]}} - add watched", flush=True)
    print(f"  POST /watched/remove  {{ids:[...]}} - remove watched", flush=True)
    print(f"  POST /watched/reload  - reread watched_ids.json", flush=True)
    print(f"Open in browser: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        worker.stop()
        server.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TBH Box Queue Reader")
    parser.add_argument("mode", choices=["cli", "http"], help="run mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass single-instance lock (DANGEROUS - may crash game)",
    )
    args = parser.parse_args()

    if not AGENT_FILE.exists():
        print(f"ERROR: agent missing at {AGENT_FILE}", file=sys.stderr)
        return 2

    if not args.force:
        if not acquire_single_instance_lock():
            print(
                "ERROR: 已有另一个 tbh_reader 在运行，不能同时启动两个实例！\n"
                "       (同时向游戏注入两个 Frida agent 会导致游戏崩溃)\n"
                "       先关掉旧的，或者用 --force 跳过此检查（不推荐）。",
                file=sys.stderr,
            )
            return 3

    if args.mode == "cli":
        return cli_mode()
    return http_mode(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
