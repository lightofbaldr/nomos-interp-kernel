"""Read-only entry, inside the improver namespace.

The only network peer is the mounted, host-side Messages gateway. All tools and
descendants stay in this namespace and the supervising service's cgroup.
"""

import os
import selectors
import socket
import socketserver
import subprocess
import sys
import threading


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(120)
            with socket.socket(socket.AF_UNIX) as upstream:
                upstream.settimeout(120)
                upstream.connect("/improver.sock")
                with selectors.DefaultSelector() as sel:
                    sel.register(upstream, selectors.EVENT_READ, self.request)
                    sel.register(self.request, selectors.EVENT_READ, upstream)
                    while events := sel.select(timeout=120):
                        for key, _ in events:
                            data = key.fileobj.recv(65536)
                            if not data:
                                return
                            key.data.sendall(data)
        except OSError:
            pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def client_env(mode, stream):
    # No credential in argv, files, diagnostics or inherited host environment.
    # The bounded anonymous input pipe closes before the model client starts.
    raw = stream.read(4097)
    if (
        mode not in ("subscription", "api-key")
        or not 16 <= len(raw) <= 4096
        or any(not 32 < c < 127 for c in raw)
    ):
        raise ValueError("invalid improver client credential input")
    token = raw.decode("ascii")
    if mode == "api-key" and token != "harness2-inert-api-placeholder":
        raise ValueError("API client requires placeholder, not a real key")
    env = dict(os.environ)
    for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    env["CLAUDE_CODE_OAUTH_TOKEN" if mode == "subscription" else "ANTHROPIC_API_KEY"] = token
    return env


def main():
    if sys.argv[1:2] != ["--auth"] or sys.argv[3:4] != ["--"] or len(sys.argv) < 5:
        return 2
    try:
        env = client_env(sys.argv[2], sys.stdin.buffer)
    except (ValueError, OSError):
        print("improver credential input refused", file=sys.stderr)
        return 2
    with Server(("127.0.0.1", 8092), Relay) as proxy:
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            with open("/runtime/mission.txt", "rb") as mission:
                return subprocess.call(sys.argv[4:], stdin=mission, env=env)
        finally:
            proxy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
