"""Trusted in-namespace entry: local TCP to the mounted model-only Unix broker.

This file is mounted read-only, invoked with python -I, and has no host secrets.
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
            with socket.socket(socket.AF_UNIX) as upstream:
                upstream.settimeout(120)
                upstream.connect("/model.sock")
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
            return


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def main():
    if sys.argv[1:2] != ["--"] or len(sys.argv) < 3:
        return 2
    with Server(("127.0.0.1", 8091), Relay) as proxy:
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            return subprocess.call(sys.argv[2:], stdin=subprocess.DEVNULL)
        finally:
            proxy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
