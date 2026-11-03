"""Sequential fake-provider regression while the live executor is reserved.

Excludes the real sandbox/compiler/Pi integration suite; its three broker-only
tests are selected explicitly. Real network connections are confined to local
fixtures, with :8091 forbidden. No sandbox or toolchain is launched by this runner.
"""

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BROKER_CONTROLS = {
    "test_broker_only_fixed_model_route_and_cap",
    "test_closed_broker_refuses_new_admission",
    "test_closing_broker_revokes_already_admitted_waiting_request",
}


def cases(suite):
    for entry in suite:
        if isinstance(entry, unittest.TestSuite):
            yield from cases(entry)
        elif type(entry).__module__ == "test_improver_namespace":
            continue
        elif (
            type(entry).__module__ != "test_sandbox_mojo"
            or entry._testMethodName in BROKER_CONTROLS
        ):
            yield entry


def guarded_connect(original):
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if address[0] not in ("127.0.0.1", "::1", "localhost") or address[1] == 8091:
                raise AssertionError("Offline regression may not contact a live provider or :8091")
        elif sock.family != socket.AF_UNIX:
            raise AssertionError("Unexpected socket family in offline regression")
        return original(sock, address)

    return connect


def main():
    suite = unittest.TestSuite(cases(unittest.defaultTestLoader.discover(str(ROOT / "tests"))))
    if suite.countTestCases() < 150:
        raise RuntimeError("Incomplete offline discovery")
    with (
        patch.object(socket.socket, "connect", guarded_connect(socket.socket.connect)),
        patch.object(socket.socket, "connect_ex", guarded_connect(socket.socket.connect_ex)),
    ):
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
