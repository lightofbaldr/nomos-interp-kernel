"""Read-only entry, inside the improver namespace. No real credentials live here.

The only network peer is the mounted, host-side Messages gateway. All tools and
descendants stay in this namespace and the supervising service's cgroup.
"""

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


def main():
    if sys.argv[1:2] != ["--"] or len(sys.argv) < 3:
        return 2
    with Server(("127.0.0.1", 8092), Relay) as proxy:
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            with open("/runtime/mission.txt", "rb") as mission:
                return subprocess.call(sys.argv[2:], stdin=mission)
        finally:
            proxy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
