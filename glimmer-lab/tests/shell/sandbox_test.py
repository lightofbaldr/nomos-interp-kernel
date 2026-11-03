"""CPU-only namespace controls. Every credential is a generated test decoy.

No model, real session DB, live lab, GPU device, real credential, or kernel build
is accessed. The optional compiler case builds the existing hello fixture only.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'runtime/files'
BROKER = SOURCE / 'shell_broker.py'
PREFIX = '/home/adam/Projects/nomos-interp-kernel/.pixi/envs/default'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


shell = module('shell_broker')


def python_command(source):
    return '/usr/bin/python3 -I -B -c ' + shlex.quote(source)


def no_canaries(text, canaries):
    if any(value in text for value in canaries):
        raise AssertionError('test-only credential canary escaped')


class ShellSandbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='glimmer-shell-test-')
        self.base = Path(self.temp.name)
        self.root = self.base / 'private'
        self.root.mkdir(mode=0o700)
        (self.root / 'workspaces').mkdir(mode=0o700)
        self.work = self.root / 'workspaces/1'
        self.work.mkdir(mode=0o700)
        self.unit = 'glimmer-files-' + hashlib.sha256(str(self.root).encode()).hexdigest()[:16] + '-1.service'

    def tearDown(self):
        shell.files.stop(self.unit)
        self.temp.cleanup()

    def arguments(self, ident='1', broker=BROKER, prefix=None):
        return ['/usr/bin/python3', '-I', '-B', str(broker), str(self.root), ident] + ([] if prefix is None else [prefix])

    def call(self, command=None, ident='1', broker=BROKER, prefix=None, request=None, env=None):
        if request is None:
            request = {} if ident == 'probe' else {'command': command}
        result = subprocess.run(self.arguments(ident, broker, prefix), input=json.dumps(request),
                                text=True, capture_output=True, timeout=110, env=env)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertLessEqual(len(result.stdout.encode()), shell.TRANSPORT_LIMIT + 1)
        data = json.loads(result.stdout)
        self.assertEqual(data['policy'], shell.POLICY)
        return result, data

    def copy_runtime(self, name, changes):
        target = self.base / name
        target.mkdir()
        for filename in ('broker.py', 'worker.py', 'mojo_broker.py', 'shell_broker.py', 'shell_entry.py'):
            shutil.copyfile(SOURCE / filename, target / filename)
        for filename, before, after, count in changes:
            path = target / filename
            original = path.read_text()
            self.assertGreaterEqual(original.count(before), count)
            changed = original.replace(before, after, count)
            self.assertNotEqual(original, changed)
            path.write_text(changed)
        return target / 'shell_broker.py'

    def settled(self):
        response = shell.files.control('show', self.unit, '--property=LoadState,ActiveState')
        fields = dict(row.split('=', 1) for row in response.stdout.decode().splitlines() if '=' in row)
        self.assertTrue(fields.get('LoadState') == 'not-found' or fields.get('ActiveState') in ('inactive', 'failed'), fields)

    def wait_for(self, path):
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(path.exists(), 'sandbox did not reach the test marker')

    def test_probe_checks_real_namespaces_and_effective_limits(self):
        process, result = self.call(ident='probe')
        self.assertEqual(process.returncode, 0, result)
        self.assertTrue(result['ok'], result)
        evidence = result['result']['evidence']
        self.assertTrue(all(evidence['isolation']['checks'].values()))
        for name in ('net', 'pid', 'mnt', 'user', 'ipc', 'uts'):
            self.assertNotEqual(evidence['isolation'][name + 'ns'], os.readlink('/proc/self/ns/' + name))
        for key, expected in {'MemoryMax': '4294967296', 'MemorySwapMax': '0', 'TasksMax': '128',
                              'CPUQuotaPerSecUSec': '2s', 'RuntimeMaxUSec': '1min 32s', 'LimitCPU': '90',
                              'LimitFSIZE': '67108864', 'LimitNOFILE': '256', 'LimitCORE': '0',
                              'KillMode': 'control-group', 'NoNewPrivileges': 'yes'}.items():
            self.assertEqual(evidence['resources'][key], expected)
        self.assertEqual(result['result']['compiler'], None)
        _, result = self.call('touch /work/never', ident='probe', request={'command': 'touch /work/never'})
        self.assertTrue(result['unavailable'])
        self.assertFalse((self.work / 'never').exists())

    def test_workspace_python_helper_private_tmp_and_stdin_eof(self):
        (self.work / 'helper.py').write_text('from pathlib import Path\nprint(Path("input").read_text().upper())\n')
        command = 'printf hello > input; python3 helper.py; printf scratch > /tmp/ephemeral; test -z "$(cat)"; pwd'
        process, result = self.call(command)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['stdout'], 'HELLO\n/work\n')
        self.assertEqual((self.work / 'input').read_text(), 'hello')
        self.assertEqual(result['result']['command_sha256'], hashlib.sha256(command.encode()).hexdigest())
        self.assertEqual(result['result']['exit_code'], 0)
        _, result = self.call('test ! -e /tmp/ephemeral && test ! -e input', ident='2')
        self.assertTrue(result['ok'], result)
        self.settled()

    def test_decoy_credential_oracle_with_positive_host_and_negative_sandbox_controls(self):
        # Exact test-owned paths only. In particular, never read the real ~/.env.
        home = self.base / 'decoy-home'
        (home / '.ssh').mkdir(parents=True)
        values = ['TEST_ONLY_' + uuid.uuid4().hex for _ in range(4)]
        paths = [home / '.env', home / '.ssh/id_ed25519', self.base / 'outside-secret']
        for path, value in zip(paths, values):
            path.write_text(value)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        script = f'''import json, os, socket
found = []
for path in {list(map(str, paths))!r}:
    try:
        with open(path) as stream: found.append(stream.read())
    except OSError: pass
found.append(os.environ.get('GLIMMER_HOST_CANARY', ''))
sock = socket.socket(); sock.settimeout(0.5)
try:
    sock.connect(('127.0.0.1', {listener.getsockname()[1]})); network = True
except OSError: network = False
sock.close()
print(json.dumps({{'found': found, 'network': network}}))
'''
        env = {**shell.files.ENV, 'GLIMMER_HOST_CANARY': values[3], 'HOME': str(home),
               'BASH_ENV': str(home / '.env'), 'PYTHONPATH': str(home)}
        try:
            control = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', script],
                                     env=env, capture_output=True, text=True, check=True, timeout=5)
            self.assertEqual(json.loads(control.stdout), {'found': values, 'network': True})
            with self.assertRaisesRegex(AssertionError, 'canary escaped'):
                no_canaries(control.stdout, values)
            for value in values:
                with self.assertRaises(AssertionError):
                    no_canaries('known positive ' + value, values)
            _, result = self.call(python_command(script), env=env)
            self.assertTrue(result['ok'], result)
            no_canaries(json.dumps(result), values)
            self.assertEqual(json.loads(result['result']['stdout']), {'found': [''], 'network': False})
            for path, value in zip(paths, values):
                self.assertEqual(path.read_text(), value)
        finally:
            listener.close()

    def test_filesystem_environment_devices_and_namespace_escape_attempts(self):
        canary = self.base / 'host-canary'
        canary.write_text('TEST_ONLY_HOST_FILE')
        script = f'''import json, os, subprocess
checks = {{}}
checks['empty_home'] = os.listdir('/home') == []
checks['empty_run'] = os.listdir('/run') == []
checks['no_gpu'] = not any(name.startswith(('nvidia', 'dri', 'kfd')) for name in os.listdir('/dev'))
checks['clean_env'] = set(os.environ) <= {{'PATH', 'LANG', 'HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'LC_CTYPE', 'PWD', 'SHLVL', '_'}}
for index, path in enumerate({[str(canary), '/work/../escape', '/etc/escape', '/home/escape', '/run/escape', '/usr/escape', '/runtime/shell_entry.py', '/dev/escape']!r}):
    try:
        with open(path, 'w') as stream: stream.write('UNEXPECTED')
        checks['readonly_' + str(index)] = False
    except OSError: checks['readonly_' + str(index)] = True
for index, path in enumerate({[str(canary), '/proc/1/root' + str(canary), '/proc/self/root' + str(canary)]!r}):
    try:
        with open(path) as stream: stream.read()
        checks['hidden_' + str(index)] = False
    except OSError: checks['hidden_' + str(index)] = True
checks['nested_userns_denied'] = subprocess.run(['/usr/bin/unshare', '-Ur', '/usr/bin/true'], capture_output=True).returncode != 0
print(json.dumps(checks))
'''
        _, result = self.call(python_command(script))
        self.assertTrue(result['ok'], result)
        checks = json.loads(result['result']['stdout'])
        self.assertEqual(len(checks), 16)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(canary.read_text(), 'TEST_ONLY_HOST_FILE')

    def test_links_special_files_and_symlinked_root_fail_before_execution(self):
        canary = self.base / 'canary'
        canary.write_text('TEST_ONLY_KEEP')
        bad = self.work / 'bad'
        for kind in ('symlink', 'parent', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'symlink': bad.symlink_to(canary)
                elif kind == 'parent': bad.symlink_to(self.base, target_is_directory=True)
                elif kind == 'hardlink': os.link(canary, bad)
                else: os.mkfifo(bad)
                _, result = self.call('printf NEVER_EXECUTED; printf changed > /work/bad')
                self.assertFalse(result['ok'])
                self.assertTrue(result['unavailable'])
                self.assertNotIn('NEVER_EXECUTED', json.dumps(result))
                self.assertEqual(canary.read_text(), 'TEST_ONLY_KEEP')
                bad.unlink()
        alias = self.base / 'linked-root'
        alias.symlink_to(self.root, target_is_directory=True)
        command = ['/usr/bin/python3', '-I', '-B', str(BROKER), str(alias), '1']
        result = subprocess.run(command, input='{"command":"touch /work/never"}', capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 3)
        self.assertFalse((self.work / 'never').exists())

    def test_invalid_requests_and_literal_host_metacharacters_have_no_host_side_effects(self):
        for request in (None, [], {}, {'command': ''}, {'command': ' \n'}, {'command': 3},
                        {'command': '\0'}, {'command': '\ud800'}, {'command': 'x' * 16385},
                        {'command': 'true', 'env': {}}, {'command': 'true', 'cwd': '/'},
                        {'command': 'true', 'timeout': 999}, {'command': 'true', 'prefix': '/usr'}):
            raw = json.dumps(request)
            process = subprocess.run(self.arguments(), input=raw, capture_output=True, text=True, timeout=5)
            self.assertEqual(process.returncode, 3, raw[:100])
            self.assertTrue(json.loads(process.stdout)['unavailable'])
        for raw in ('{"command":"true","command":"false"}', '{', ' ' * 131073, '[' * 2000 + ']' * 2000):
            process = subprocess.run(self.arguments(), input=raw, capture_output=True, text=True, timeout=5)
            self.assertEqual(process.returncode, 3)
        host_target = self.base / 'host-must-not-exist'
        _, result = self.call('printf shell; touch ' + shlex.quote(str(host_target)))
        self.assertFalse(result['ok'])
        self.assertFalse(host_target.exists())
        # A large escaped JSON payload must drain without a host pipe deadlock.
        command = '#' + '\u00e9' * 8000 + '\nprintf LARGE_REQUEST_OK\n'
        _, result = self.call(command)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['stdout'], 'LARGE_REQUEST_OK')

    def test_untrusted_stdout_cannot_forge_metadata_or_success(self):
        command = "printf '%s\\n' '{\"ready\":{}}' '{\"metadata\":{\"probe\":true}}' '{\"ok\":true}'; exit 7"
        process, result = self.call(command)
        self.assertEqual(process.returncode, 0)  # a valid tool result, not broker failure
        self.assertFalse(result['ok'])
        self.assertEqual(result['result']['exit_code'], 7)
        self.assertEqual(result['result']['probe'], False)
        self.assertIn('{"ok":true}', result['result']['stdout'])
        self.assertTrue(result['result']['evidence']['isolation']['checks']['work_rw'])

    def test_host_file_descriptors_are_not_inherited_by_the_command(self):
        value = 'TEST_ONLY_DESCRIPTOR_' + uuid.uuid4().hex
        canary = self.base / 'descriptor-canary'
        canary.write_text(value)
        fd = os.open(canary, os.O_RDONLY)
        script = '''import json, os, stat
found = []
for name in os.listdir('/proc/self/fd'):
    fd = int(name)
    if fd <= 2: continue
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode): found.append(os.read(fd, 4096).decode(errors='replace'))
    except OSError: pass
print(json.dumps(found))
'''
        try:
            # Positive control for the same passed descriptor, with no real data.
            host = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', script], pass_fds=(fd,),
                                  capture_output=True, text=True, check=True, timeout=5)
            with self.assertRaises(AssertionError):
                no_canaries(host.stdout, [value])
            os.lseek(fd, 0, os.SEEK_SET)
            process = subprocess.run(self.arguments(), pass_fds=(fd,), capture_output=True, text=True,
                                     input=json.dumps({'command': python_command(script)}), timeout=15)
            result = json.loads(process.stdout)
            self.assertTrue(result['ok'], result)
            no_canaries(process.stdout + process.stderr, [value])
            self.assertEqual(json.loads(result['result']['stdout']), [])
        finally:
            os.close(fd)

    def test_stream_and_json_transport_bounds(self):
        for fd in (1, 2):
            with self.subTest(stream=fd):
                _, result = self.call(python_command(f'import os\nwhile True: os.write({fd}, b"x" * 8192)'))
                self.assertFalse(result['ok'])
                self.assertEqual(result['result']['reason'], 'output_limit')
                self.assertLessEqual(result['result']['captured_bytes']['stdout'], 16384)
                self.assertLessEqual(result['result']['captured_bytes']['stderr'], 16384)
                self.settled()
        _, result = self.call(python_command('import os; os.write(1, b"\\0" * 16000); os.write(2, b"\\0" * 16000)'))
        self.assertFalse(result['ok'])
        self.assertTrue(result['result']['transport_truncated'])
        self.assertEqual(result['result']['reason'], 'transport_limit')

    def test_missing_executor_failed_isolation_and_bad_limits_do_not_admit_command(self):
        cases = [
            ('missing', [('shell_broker.py', "'/usr/bin/bwrap'", "'/missing-bwrap'", 1)]),
            ('isolation', [('shell_entry.py', "'work_rw': 'rw' in mounts.get('/work', [])", "'work_rw': False", 1)]),
            ('limits', [('shell_broker.py', "'MemoryMax': '4294967296'", "'MemoryMax': '67108864'", 1)]),
        ]
        for name, changes in cases:
            with self.subTest(case=name):
                copied = self.copy_runtime(name, changes)
                process, result = self.call('touch /work/never', broker=copied)
                self.assertEqual(process.returncode, 3)
                self.assertTrue(result['unavailable'])
                self.assertFalse((self.work / 'never').exists())
                self.settled()

    def test_fork_task_limit_file_limit_and_workspace_aggregate_limit(self):
        script = '''import errno, json, os, time
children = []
denied = False
for _ in range(150):
    try: pid = os.fork()
    except OSError as error:
        denied = error.errno == errno.EAGAIN
        break
    if pid == 0:
        time.sleep(20)
        os._exit(0)
    children.append(pid)
print(json.dumps({'denied': denied, 'children': len(children)}), flush=True)
'''
        _, result = self.call(python_command(script))
        self.assertTrue(result['ok'], result)
        fork = json.loads(result['result']['stdout'])
        self.assertTrue(fork['denied'])
        self.assertGreater(fork['children'], 0)
        self.assertLess(fork['children'], 128)
        self.settled()
        _, result = self.call(python_command('import os\nf = os.open("large", os.O_CREAT | os.O_WRONLY, 0o600)\nos.ftruncate(f, 67108865)'))
        self.assertFalse(result['ok'])
        self.assertLessEqual((self.work / 'large').stat().st_size, 67108864)
        _, result = self.call(python_command('import os\nfor n in range(5):\n f=os.open("sparse"+str(n), os.O_CREAT | os.O_WRONLY, 0o600); os.ftruncate(f, 67108864); os.close(f)'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['result']['reason'], 'workspace_limit')

    def test_bounded_memory_oom_and_private_tmp_quota_controls(self):
        # Exercise OOM at 64 MiB, not by pressuring the laptop with 4 GiB.
        memory = self.copy_runtime('memory', [('shell_broker.py', "'MemoryMax': '4294967296'", "'MemoryMax': '67108864'", 2)])
        _, result = self.call(python_command('x = bytearray(128 * 1024**2); print("UNEXPECTED")'), broker=memory)
        self.assertFalse(result['ok'])
        self.assertNotIn('UNEXPECTED', json.dumps(result))
        self.settled()
        # The normal probe measures the real 256 MiB mount; this tests ENOSPC cheaply.
        scratch = self.copy_runtime('scratch', [('shell_entry.py', 'TMP_LIMIT = 256 * 1024**2', 'TMP_LIMIT = 1024**2', 1)])
        _, result = self.call(python_command('from pathlib import Path; Path("/tmp/large").write_bytes(b"x" * (2 * 1024**2))'), broker=scratch)
        self.assertFalse(result['ok'])
        self.assertIn('No space left on device', result['result']['stderr'])
        _, result = self.call('printf RECOVERED')
        self.assertTrue(result['ok'], result)

    def test_shortened_wall_deadline_kills_detached_descendants(self):
        copied = self.copy_runtime('deadline', [
            ('shell_broker.py', 'EXEC_SECONDS = 90', 'EXEC_SECONDS = 1', 1),
            ('shell_broker.py', "'RuntimeMaxUSec': '1min 32s'", "'RuntimeMaxUSec': '3s'", 1),
        ])
        child = python_command('import time; from pathlib import Path; time.sleep(2); Path("/work/late").write_text("BAD")')
        command = 'setsid ' + child + ' </dev/null >/dev/null 2>&1 &\nprintf started > /work/started\nsleep 20'
        start = time.monotonic()
        _, result = self.call(command, broker=copied)
        self.assertFalse(result['ok'])
        self.assertEqual(result['result']['reason'], 'timeout')
        self.assertLess(time.monotonic() - start, 10)
        self.settled()
        time.sleep(2.1)
        self.assertFalse((self.work / 'late').exists())
        _, result = self.call('printf RECOVERED')
        self.assertTrue(result['ok'], result)

    def test_cpu_hard_limit_and_independent_systemd_deadline_controls(self):
        cpu = self.copy_runtime('cpu', [
            ('shell_broker.py', "'LimitCPU': '90'", "'LimitCPU': '1'", 2),
            ('shell_entry.py', 'CPU_SECONDS = 90', 'CPU_SECONDS = 1', 1),
        ])
        started = time.monotonic()
        _, result = self.call(python_command('while True: pass'), broker=cpu)
        self.assertFalse(result['ok'])
        self.assertNotEqual(result['result']['exit_code'], 0)
        self.assertIsNone(result['result']['reason'])
        self.assertLess(time.monotonic() - started, 8)
        self.settled()
        # Keep the host watchdog at 90s, but exercise systemd's independent
        # RuntimeMaxSec at 2s. The actual 92s property is read back by normal probes.
        service = self.copy_runtime('service-deadline', [
            ('shell_broker.py', "'RuntimeMaxSec': str(EXEC_SECONDS + 2)", "'RuntimeMaxSec': '2'", 1),
            ('shell_broker.py', "'RuntimeMaxUSec': '1min 32s'", "'RuntimeMaxUSec': '2s'", 1),
        ])
        started = time.monotonic()
        _, result = self.call('sleep 20', broker=service)
        self.assertFalse(result['ok'])
        self.assertNotEqual(result['result']['exit_code'], 0)
        self.assertIsNone(result['result']['reason'])
        self.assertLess(time.monotonic() - started, 8)
        self.settled()

    def test_abort_and_killed_broker_recovery_join_service_before_reusing_lock(self):
        child = python_command('import time; from pathlib import Path; time.sleep(2); Path("/work/late").write_text("BAD")')
        for signum in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=signum):
                started = self.work / 'started'
                started.unlink(missing_ok=True)
                command = 'setsid ' + child + ' </dev/null >/dev/null 2>&1 &\nprintf started > /work/started\nsleep 20'
                process = subprocess.Popen(self.arguments(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                process.stdin.write(json.dumps({'command': command}))
                process.stdin.close()
                try:
                    self.wait_for(started)
                    process.send_signal(signum)
                    process.wait(timeout=12)
                    self.assertNotEqual(process.returncode, 0)
                    if signum == signal.SIGTERM:
                        self.settled()
                    # SIGKILL cannot run finally; the next call settles the exact
                    # shared service under the acquired lock before releasing input.
                    _, result = self.call('printf RECOVERED')
                    self.assertTrue(result['ok'], result)
                    self.settled()
                    time.sleep(2.1)
                    self.assertFalse((self.work / 'late').exists())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    shell.files.stop(self.unit)
                    process.stdout.close()
                    process.stderr.close()

    def test_abort_during_launcher_creation_never_admits_command_or_leaves_service(self):
        marker = self.base / 'launcher-created'
        wrapper = f'''def delayed_test_launch(*args, **kwargs):
    child = subprocess.Popen(*args, **kwargs)
    Path({str(marker)!r}).write_text('created')
    time.sleep(0.5)
    return child


def run(work_fd, source, unit, request, config=None, prefix=None, probe=False):'''
        copied = self.copy_runtime('launch-cancel', [
            ('shell_broker.py', 'def run(work_fd, source, unit, request, config=None, prefix=None, probe=False):', wrapper, 1),
            ('shell_broker.py', 'process = subprocess.Popen(argv(', 'process = delayed_test_launch(argv(', 1),
        ])
        process = subprocess.Popen(self.arguments(broker=copied), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process.stdin.write(json.dumps({'command': 'touch /work/never'}))
        process.stdin.close()
        try:
            self.wait_for(marker)
            process.terminate()
            process.wait(timeout=12)
            self.assertEqual(process.returncode, 3)
            result = json.loads(process.stdout.read())
            self.assertTrue(result['unavailable'])
            self.assertFalse((self.work / 'never').exists())
            self.settled()
            _, result = self.call('true')
            self.assertTrue(result['ok'], result)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            shell.files.stop(self.unit)
            process.stdout.close()
            process.stderr.close()

    def test_shared_file_tool_lock_serializes_shell_mutations(self):
        command = 'printf started > /work/started; sleep 0.5; printf finished > /work/state'
        with concurrent.futures.ThreadPoolExecutor() as pool:
            running = pool.submit(self.call, command)
            self.wait_for(self.work / 'started')
            result = subprocess.run(['/usr/bin/python3', '-I', '-B', str(SOURCE / 'broker.py'), str(self.root), '1'],
                                    input='{"op":"read","args":{"path":"state"}}', capture_output=True, text=True, timeout=15)
            self.assertTrue(running.result()[1]['ok'])
        self.assertEqual(json.loads(result.stdout)['result']['text'], 'finished')

    def test_descriptor_budget_oracles_have_positive_and_negative_controls(self):
        (self.work / 'one').write_text('1234')
        fd = os.open(self.work, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.assertEqual(shell.entry.workspace_budget(fd), {'entries': 1, 'bytes': 4})
            with patch.object(shell.entry, 'WORK_LIMIT', 3), self.assertRaisesRegex(ValueError, 'byte limit'):
                shell.entry.workspace_budget(fd)
            with patch.object(shell.entry, 'ENTRY_LIMIT', 0), self.assertRaisesRegex(ValueError, 'entry limit'):
                shell.entry.workspace_budget(fd)
            (self.work / 'directory').mkdir()
            with patch.object(shell.entry, 'DEPTH_LIMIT', 0), self.assertRaisesRegex(ValueError, 'depth limit'):
                shell.entry.workspace_budget(fd)
        finally:
            os.close(fd)

    def test_existing_hello_fixture_runs_through_python_checker_and_readonly_compiler(self):
        # This is a tiny CPU compiler fixture, not check_mojo.py parity or a kernel build.
        (self.work / 'main.mojo').write_bytes((ROOT / 'tests/mojo/fixtures/hello.mojo').read_bytes())
        (self.work / 'check_mojo.py').write_text('''import subprocess
from pathlib import Path
subprocess.run(['mojo', 'build', '/work/main.mojo', '-o', '/tmp/program'], check=True)
subprocess.run(['/tmp/program'], check=True)
for path in ['/opt/mojo/bin/mojo', '/config/modular.cfg']:
    try:
        with open(path, 'ab'): pass
    except OSError: continue
    raise RuntimeError('compiler/config is writable')
''')
        _, result = self.call('python3 check_mojo.py', prefix=PREFIX)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['stdout'].strip(), 'glimmer-mojo-ok')
        self.assertTrue(result['result']['evidence']['isolation']['checks']['compiler_ro'])
        self.assertEqual(set(result['result']['compiler']['files']), {'bin/mojo', 'bin/lld', 'lib/mojo/std.mojoc', 'share/max/modular.cfg'})
        _, probe = self.call(ident='probe', prefix=PREFIX)
        self.assertTrue(probe['ok'], probe)
        self.assertTrue(probe['result']['stdout'].strip())
        _, missing = self.call('true', prefix=str(self.base / 'missing-compiler'))
        self.assertTrue(missing['unavailable'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
