import unittest
from pathlib import Path

from harness2.benchmarks.mojo_grade import grade
from harness2.benchmarks.mojo_tasks import Task
from harness2.substrate.sandbox import ProcessResult, Sandbox


class FakeSandbox:
    def __init__(self, compile_rc=0, stdout="7\n", reason=None, health_rc=0):
        self.compile_rc, self.stdout, self.reason = compile_rc, stdout, reason
        self.health_rc = health_rc
        self.commands = []
        self.health_calls = 0

    def identity(self):
        return {"fake_compiler": self.health_rc}

    def run(self, work, args, **kw):
        if "h2-mojo-health-" in work.name:
            self.health_calls += 1
            if "build" in args:
                (work / "program").write_text("fake")
            return ProcessResult(self.health_rc, "harness2-toolchain-ok\n", "", 0)
        self.commands.append(args)
        if "build" in args:
            (work / "program").write_text("fake")
            return ProcessResult(self.compile_rc, "", "", 0, self.reason)
        return ProcessResult(0, self.stdout, "", 0, self.reason)


class Grader(unittest.TestCase):
    def task(self, **kw):
        return Task(
            **(
                {
                    "id": "x",
                    "prompt": "print seven",
                    "kind": "run",
                    "split": "train",
                    "expect_stdout": "7",
                }
                | kw
            )
        )

    def test_compile_and_stdout_both_required(self):
        self.assertTrue(grade(self.task(), "code", FakeSandbox())["passed"])
        self.assertFalse(grade(self.task(), "code", FakeSandbox(stdout="8"))["passed"])
        s = FakeSandbox(compile_rc=1)
        self.assertFalse(grade(self.task(), "code", s)["passed"])
        self.assertEqual(len(s.commands), 1)

    def test_compile_only_never_runs(self):
        s = FakeSandbox()
        self.assertTrue(grade(self.task(kind="compile"), "code", s)["passed"])
        self.assertEqual(len(s.commands), 1)

    def test_outage_is_not_zero(self):
        r = grade(self.task(), "code", FakeSandbox(reason="sandbox"))
        self.assertEqual(r["status"], "unscored")
        self.assertIsNone(r["passed"])

    def test_empty_program_is_observed_failure(self):
        self.assertFalse(grade(self.task(), None, FakeSandbox())["passed"])

    def test_broken_compiler_control_is_unscored_not_candidate_failure(self):
        s = FakeSandbox(health_rc=1)
        result = grade(self.task(), "code", s)
        self.assertEqual(result["reason"], "toolchain_health")
        self.assertIsNone(result["passed"])
        self.assertEqual(s.commands, [])

    def test_toolchain_control_cache_is_bound_to_identity(self):
        s = FakeSandbox()
        grade(self.task(), "code", s)
        grade(self.task(), "code", s)
        self.assertEqual(s.health_calls, 2)
        s.health_rc = 1
        result = grade(self.task(), "code", s)
        self.assertEqual(s.health_calls, 3)
        self.assertEqual(result["status"], "unscored")

    def test_real_compiler_inside_no_network_sandbox(self):
        prefix = Path.home() / ".pixi/envs/mojo"
        self.assertTrue((prefix / "bin/mojo").is_file(), "real offline compiler prerequisite")
        s = Sandbox(mojo=prefix)
        for code, expected in [
            ("def main():\n    print(7)\n", True),
            ("def main():\n    print(8)\n", False),
            ("this is not Mojo\n", False),
        ]:
            with self.subTest(code=code):
                result = grade(self.task(), code, s)
                self.assertEqual(result["status"], "scored", result)
                self.assertEqual(result["passed"], expected, result)

    def test_function_test_main_is_not_in_rollout(self):
        s = Sandbox(mojo=Path.home() / ".pixi/envs/mojo")
        t = self.task(
            kind="function",
            expect_stdout="ok",
            test='def main() raises:\n    if seven() != 7:\n        raise Error("bad")\n    print("ok")',
        )
        self.assertTrue(grade(t, "def seven() -> Int:\n    return 7\n", s)["passed"])
        self.assertFalse(grade(t, "def seven() -> Int:\n    return 8\n", s)["passed"])
