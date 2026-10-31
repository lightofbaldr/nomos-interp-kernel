"""CPU-only tests: real transient services, never a model or the Nomos port."""
import concurrent.futures
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "runtime/files"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker, broker = load("worker"), load("broker")


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="glimmer-files-test-")
        self.base = Path(self.temp.name)
        self.root = self.base / "private"

    def tearDown(self):
        self.temp.cleanup()

    def call(self, op, args=None, ident="1", source=SOURCE):
        r = subprocess.run(["/usr/bin/python3", "-I", "-B", str(source / "broker.py"), str(self.root), ident],
                           input=json.dumps({"op": op, "args": args or {}}), text=True, capture_output=True, timeout=45)
        self.assertEqual(r.stderr, "")
        return json.loads(r.stdout)

    def test_real_preflight_isolation_and_effective_cgroup_limits(self):
        p = self.call("probe", ident="probe")
        self.assertTrue(p["ok"], p)
        self.assertTrue(all(p["evidence"]["isolation"]["checks"].values()))
        self.assertNotEqual(p["evidence"]["isolation"]["netns"], os.readlink("/proc/self/ns/net"))
        self.assertEqual(p["evidence"]["resources"]["MemoryMax"], "268435456")
        self.assertEqual(p["evidence"]["resources"]["KillMode"], "control-group")
        self.assertIn("not-found", broker.control("show", p["unit"], "--property=LoadState").stdout.decode())

    def test_six_operations_and_separate_workspaces(self):
        self.assertTrue(self.call("write", {"path": "nested/main.mojo", "content": "one\ntwo\n"})["ok"])
        self.assertEqual(self.call("read", {"path": "/work/nested/main.mojo", "offset": 4, "limit": 3})["result"]["text"], "two")
        self.assertTrue(self.call("edit", {"path": "nested/main.mojo", "oldText": "two", "newText": "three"})["ok"])
        self.assertEqual(self.call("ls", {"path": "."})["result"]["entries"][0]["kind"], "directory")
        self.assertEqual(self.call("find", {"path": ".", "name": ".mojo"})["result"]["entries"][0]["path"], "nested/main.mojo")
        self.assertEqual(self.call("grep", {"path": ".", "text": "three"})["result"]["entries"][0]["line"], 2)
        self.assertFalse(self.call("read", {"path": "nested/main.mojo"}, ident="2")["ok"])
        self.assertFalse(self.call("edit", {"path": "nested/main.mojo", "oldText": "e", "newText": "X"})["ok"])

    def test_traversal_unknown_ops_and_environment_do_not_escape(self):
        outside = self.base / "host-canary"
        outside.write_text("PRIVATE_CANARY")
        for p in (str(outside), "/etc/passwd", "../host-canary", "a/../../host-canary", "/work/../host-canary", "a//b", "a\\b", ".glimmer-reserved"):
            with self.subTest(path=p):
                self.assertFalse(self.call("write", {"path": p, "content": "OVERWRITE"})["ok"])
        self.assertEqual(outside.read_text(), "PRIVATE_CANARY")
        self.assertFalse(self.call("bash", {"command": "touch /work/nope"})["ok"])
        self.assertFalse(self.call("write", {"path": "a", "content": "x", "command": "malicious"})["ok"])
        self.assertTrue(self.call("write", {"path": "literal-$(touch PWN)", "content": "not a shell"})["ok"])
        self.assertFalse((self.root / "workspaces/1/PWN").exists())
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "TEST_CANARY_ONLY", "PYTHONPATH": str(self.base)}):
            self.assertTrue(self.call("probe", ident="probe")["ok"])

    def test_symlinks_hardlinks_and_special_files_are_refused(self):
        self.call("ls", {"path": "."})
        work = self.root / "workspaces/1"
        outside = self.base / "canary"
        outside.write_text("outside")
        for kind in ("symlink", "parent", "hardlink", "fifo"):
            p = work / "bad"
            if kind == "symlink": p.symlink_to(outside)
            if kind == "parent": p.symlink_to(self.base, target_is_directory=True)
            if kind == "hardlink": os.link(outside, p)
            if kind == "fifo": os.mkfifo(p)
            self.assertFalse(self.call("read", {"path": "bad/canary" if kind == "parent" else "bad"})["ok"])
            self.assertFalse(self.call("write", {"path": "bad", "content": "overwrite"})["ok"])
            p.unlink()
        self.assertEqual(outside.read_text(), "outside")

    def test_no_follow_survives_symlink_churn(self):
        self.call("ls", {"path": "."})
        outside = self.base / "canary"
        outside.write_text("outside")
        target = self.root / "workspaces/1/moving"
        def churn():
            for _ in range(2500):
                try: target.symlink_to(outside)
                except FileExistsError: pass
                try: target.unlink()
                except FileNotFoundError: pass
        with concurrent.futures.ThreadPoolExecutor() as pool:
            moving = pool.submit(churn)
            for _ in range(4):
                r = self.call("read", {"path": "moving"})
                self.assertFalse(r["ok"])
            moving.result()
        self.assertEqual(outside.read_text(), "outside")

    def test_file_size_unicode_and_mutation_bounds(self):
        self.assertFalse(self.call("write", {"path": "big", "content": "x" * (worker.FILE_MAX + 1)})["ok"])
        self.assertTrue(self.call("write", {"path": "ok", "content": "x" * worker.FILE_MAX})["ok"])
        self.assertTrue(self.call("read", {"path": "ok"})["result"]["truncated"])
        for text in ("\0", "\ud800"):
            self.assertFalse(self.call("write", {"path": "bad", "content": text})["ok"])
        work = self.root / "workspaces/1"
        (work / "bad").write_bytes(b"\xff")
        self.assertFalse(self.call("read", {"path": "bad"})["ok"])

    def test_inventory_budgets_and_atomic_ambiguous_edit(self):
        work = self.base / "unit"
        work.mkdir()
        (work / "a").write_text("same same")
        fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(ValueError, "exactly one"):
                worker.execute(fd, {"op": "edit", "args": {"path": "a", "oldText": "same", "newText": "changed"}})
            self.assertEqual((work / "a").read_text(), "same same")
            with patch.object(worker, "WORK_MAX", 8), self.assertRaisesRegex(ValueError, "budget"):
                worker.inventory(fd)
            with patch.object(worker, "ENTRIES_MAX", 1), self.assertRaisesRegex(ValueError, "budget"):
                worker.execute(fd, {"op": "write", "args": {"path": "new", "content": "x"}})
            self.assertFalse((work / "new").exists())
        finally:
            os.close(fd)

    def test_cross_process_lock_serializes_writes(self):
        self.call("write", {"path": "v", "content": "original"})
        def edit(): return self.call("edit", {"path": "v", "oldText": "original", "newText": "replaced"})["ok"]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = list(pool.map(lambda _: edit(), range(4)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.call("read", {"path": "v"})["result"]["text"], "replaced")

    def test_recovery_discards_only_uncommitted_internal_temporary_not_the_destination(self):
        self.call("write", {"path": "v", "content": "old"})
        work = self.root / "workspaces/1"
        temp = work / (".glimmer-" + "a" * 32)
        temp.write_text("interrupted, never committed")
        temp.chmod(0o600)
        self.assertEqual(self.call("read", {"path": "v"})["result"]["text"], "old")
        self.assertFalse(temp.exists())
        canary = self.base / "canary"
        canary.write_text("keep")
        os.link(canary, temp)
        self.assertFalse(self.call("read", {"path": "v"})["ok"])
        self.assertTrue(temp.exists())
        self.assertEqual(canary.read_text(), "keep")

    def test_missing_bwrap_and_failed_probe_have_no_host_fallback(self):
        source = self.base / "broken"
        source.mkdir()
        (source / "worker.py").write_bytes((SOURCE / "worker.py").read_bytes())
        (source / "broker.py").write_text((SOURCE / "broker.py").read_text().replace('"/usr/bin/bwrap"', '"/missing-bwrap"'))
        result = self.call("write", {"path": "never", "content": "no"}, source=source)
        self.assertFalse(result["ok"])
        self.assertTrue(result["unavailable"])
        self.assertFalse((self.root / "workspaces/1/never").exists())
        (source / "broker.py").write_bytes((SOURCE / "broker.py").read_bytes())
        (source / "worker.py").write_text((SOURCE / "worker.py").read_text().replace('"root_ro": "ro" in mounts.get("/", [])', '"root_ro": False'))
        self.assertFalse(self.call("write", {"path": "never", "content": "no"}, source=source)["ok"])
        self.assertFalse((self.root / "workspaces/1/never").exists())

    def test_cgroup_property_failure_stops_before_mutation(self):
        source = self.base / "badlimits"
        source.mkdir()
        (source / "worker.py").write_bytes((SOURCE / "worker.py").read_bytes())
        # Change the effective command, not the verifier's expected value.
        (source / "broker.py").write_text((SOURCE / "broker.py").read_text().replace('"MemoryMax": "268435456"', '"MemoryMax": "134217728"', 1))
        self.assertFalse(self.call("write", {"path": "never", "content": "no"}, source=source)["ok"])
        self.assertFalse((self.root / "workspaces/1/never").exists())

    def test_abort_stops_and_joins_worker_descendants(self):
        source = self.base / "held"
        source.mkdir()
        (source / "broker.py").write_bytes((SOURCE / "broker.py").read_bytes())
        (source / "worker.py").write_text((SOURCE / "worker.py").read_text().replace('request = json.loads(line)', 'import subprocess, time\n    subprocess.Popen(["/usr/bin/python3", "-I", "-c", "import time;time.sleep(120)"])\n    open("/work/started", "w").write("yes")\n    time.sleep(120)\n    request = json.loads(line)'))
        proc = subprocess.Popen(["/usr/bin/python3", "-I", "-B", str(source / "broker.py"), str(self.root), "1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.stdin.write(b'{"op":"ls","args":{"path":"."}}')
        proc.stdin.close()
        try:
            deadline = time.monotonic() + 10
            while not (self.root / "workspaces/1/started").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root / "workspaces/1/started").exists())
            proc.terminate()
            proc.wait(timeout=10)
            self.assertEqual(proc.returncode, 3)
            unit = "glimmer-files-" + broker.hashlib.sha256(str(self.root).encode()).hexdigest()[:16] + "-1.service"
            self.assertIn("not-found", broker.control("show", unit, "--property=LoadState").stdout.decode())
            self.assertTrue(self.call("ls", {"path": "."})["ok"], "new operation can run only after joined cleanup")
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()

    def test_private_root_and_workspace_link_refused(self):
        self.root.symlink_to(self.base, target_is_directory=True)
        self.assertFalse(self.call("probe", ident="probe")["ok"])
        self.root.unlink()
        self.call("ls", {"path": "."})
        work = self.root / "workspaces/1"
        work.rmdir()
        work.symlink_to(self.base, target_is_directory=True)
        self.assertFalse(self.call("write", {"path": "never", "content": "x"})["ok"])
        self.assertFalse((self.base / "never").exists())

    def test_memory_cap_kills_only_the_worker_service(self):
        source = self.base / "memory-probe"
        source.mkdir()
        (source / "broker.py").write_bytes((SOURCE / "broker.py").read_bytes())
        (source / "worker.py").write_text((SOURCE / "worker.py").read_text().replace('request = json.loads(line)', 'oversized = bytearray(512 * 1024 * 1024)\n    request = json.loads(line)'))
        result = self.call("write", {"path": "never", "content": "x"}, source=source)
        self.assertFalse(result["ok"])
        self.assertTrue(result["unavailable"])
        self.assertFalse((self.root / "workspaces/1/never").exists())
        self.assertTrue(self.call("ls", {"path": "."})["ok"])

    def test_runtime_cap_terminates_a_hung_worker_and_allows_the_next_operation(self):
        source = self.base / "time-probe"
        source.mkdir()
        (source / "broker.py").write_bytes((SOURCE / "broker.py").read_bytes())
        (source / "worker.py").write_text((SOURCE / "worker.py").read_text().replace('request = json.loads(line)', 'import time\n    time.sleep(120)\n    request = json.loads(line)'))
        start = time.monotonic()
        result = self.call("write", {"path": "never", "content": "x"}, source=source)
        self.assertFalse(result["ok"])
        self.assertLess(time.monotonic() - start, 40)
        self.assertFalse((self.root / "workspaces/1/never").exists())
        self.assertTrue(self.call("ls", {"path": "."})["ok"])


if __name__ == "__main__":
    unittest.main()
