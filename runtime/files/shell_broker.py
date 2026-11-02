"""Disabled-by-default shell foundation; no unsandboxed execution path.

Application enablement belongs to the server operator switch and profile gate.
Only JSON crosses into the trusted entry; bash parses it *inside* bwrap after
isolation and cgroup limits have been independently confirmed by this broker.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broker as files
import mojo_broker as mojo
import shell_entry as entry

POLICY = 'glimmer.shell/bwrap-1'
EXEC_SECONDS = 90
OUTPUT_LIMIT = 16384  # raw bytes per stream, before UTF-8 replacement/JSON
PROTOCOL_LIMIT = 8192
TRANSPORT_LIMIT = 60000
PROPERTIES = {
    'MemoryMax': '4294967296', 'MemorySwapMax': '0', 'TasksMax': '128', 'CPUQuota': '200%',
    'LimitFSIZE': '67108864', 'LimitNOFILE': '256', 'LimitCORE': '0', 'LimitCPU': '90',
    'TimeoutStopSec': '2', 'KillMode': 'control-group', 'OOMPolicy': 'stop', 'NoNewPrivileges': 'yes',
}


def argv(source, unit, config=None, prefix=None, probe=False):
    command = ['/usr/bin/systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
               '--service-type=exec', '--unit=' + unit]
    command += ['--property=' + key + '=' + value
                for key, value in {**PROPERTIES, 'RuntimeMaxSec': str(EXEC_SECONDS + 2)}.items()]
    command += ['--', '/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--disable-userns',
                '--assert-userns-disabled', '--new-session', '--die-with-parent', '--cap-drop', 'ALL',
                '--clearenv', '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
                '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
                '--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev', '--remount-ro', '/dev',
                '--dir', '/home', '--dir', '/etc', '--dir', '/run', '--bind', source, '/work',
                '--size', str(entry.TMP_LIMIT), '--tmpfs', '/tmp',
                '--ro-bind', str(Path(__file__).resolve().with_name('shell_entry.py')), '/runtime/shell_entry.py']
    if prefix is not None:
        command += ['--ro-bind', str(config), '/config']
        for name in ('cc', 'c++', 'cpp'):
            link = Path('/etc/alternatives') / name
            if link.exists():
                target = link.resolve(strict=True)
                if not target.is_relative_to('/usr'):
                    raise files.Refusal('system linker outside /usr')
                command += ['--symlink', str(target), str(link)]
        for name in ('bin', 'lib', 'include'):
            command += ['--ro-bind', str(prefix / name), '/opt/mojo/' + name]
    environment = {'PATH': '/opt/mojo/bin:/usr/bin:/bin' if prefix is not None else '/usr/bin:/bin',
                   'LANG': 'C.UTF-8', 'HOME': '/home', 'TMPDIR': '/tmp', 'XDG_CACHE_HOME': '/tmp/cache'}
    if prefix is not None:
        environment['MODULAR_HOME'] = '/config'
    for key, value in environment.items():
        command += ['--setenv', key, value]
    return command + ['--chdir', '/work', '--remount-ro', '/', '--', '/usr/bin/python3', '-I', '-B',
                      '/runtime/shell_entry.py', 'probe' if probe else 'execute', 'mojo' if prefix is not None else 'basic']


def verify_ready(line, unit):
    ready = json.loads(line).get('ready')
    keys = {'root_ro', 'runtime_ro', 'entry_ro', 'work_rw', 'tmp_rw', 'proc_ro', 'dev_ro',
            'caps_zero', 'no_new_privs', 'home_empty', 'run_empty', 'cwd', 'env', 'fsize',
            'nofile', 'core', 'cpu', 'tmp_limit', 'compiler_ro', 'config_ro'}
    if (not isinstance(ready, dict) or set(ready.get('checks', {})) != keys
            or any(value is not True for value in ready['checks'].values())):
        raise files.Refusal('shell sandbox isolation probe failed')
    for namespace in ('net', 'pid', 'mnt', 'user', 'ipc', 'uts'):
        value = ready.get(namespace + 'ns')
        if (not isinstance(value, str) or not re.fullmatch(namespace + r':\[\d+\]', value)
                or value == os.readlink('/proc/self/ns/' + namespace)):
            raise files.Refusal('shell namespace not isolated')
    # Independent expected values: changing the launch policy alone cannot pass.
    expected = {'MemoryMax': '4294967296', 'MemorySwapMax': '0', 'TasksMax': '128',
                'LimitFSIZE': '67108864', 'LimitNOFILE': '256', 'LimitCORE': '0', 'LimitCPU': '90',
                'CPUQuotaPerSecUSec': '2s', 'RuntimeMaxUSec': '1min 32s', 'TimeoutStopUSec': '2s',
                'KillMode': 'control-group', 'OOMPolicy': 'stop', 'NoNewPrivileges': 'yes', 'ActiveState': 'active'}
    response = files.control('show', unit, '--property=' + ','.join(expected))
    actual = dict(row.split('=', 1) for row in response.stdout.decode().splitlines() if '=' in row)
    if response.returncode or any(actual.get(key) != value for key, value in expected.items()):
        raise files.Refusal('shell cgroup/resource properties not confirmed')
    return {'isolation': ready, 'resources': actual}


def run(work_fd, source, unit, request, config=None, prefix=None, probe=False):
    process = None
    cancelled = False

    def defer_cancel(_signum, _frame):
        nonlocal cancelled
        cancelled = True

    # Defer cancellation through orphan settlement/Popen, not through admission.
    # This keeps the newly created launcher handle reachable for cleanup even
    # when a TERM arrives between process creation and assignment to `process`.
    previous_term = signal.signal(signal.SIGTERM, defer_cancel)
    previous_int = signal.signal(signal.SIGINT, defer_cancel)
    try:
        files.stop(unit)  # settle any file/Mojo/shell orphan under the same lock/unit
        if (Path(source).resolve(strict=True) != Path(source)
                or (os.stat(source).st_dev, os.stat(source).st_ino) != (os.fstat(work_fd).st_dev, os.fstat(work_fd).st_ino)):
            raise files.Refusal('workspace path changed')
        entry.workspace_budget(work_fd)
        if cancelled:
            raise files.Refusal('operation cancelled before shell admission')
        process = subprocess.Popen(argv(source, unit, config, prefix, probe), env=files.ENV,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, close_fds=True)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        if cancelled:
            raise files.Refusal('operation cancelled before shell admission')
        chunks = {'stdout': bytearray(), 'stderr': bytearray()}
        protocol = bytearray()
        evidence = metadata = reason = None
        sending = memoryview(json.dumps(request, ensure_ascii=True, allow_nan=False).encode() + b'\n')
        start = time.monotonic()
        with selectors.DefaultSelector() as selector:
            for name in chunks:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            next_scan = start
            while selector.get_map():
                now = time.monotonic()
                if now - start > EXEC_SECONDS:
                    reason = 'timeout'
                    break
                if now >= next_scan:
                    next_scan = now + 0.25
                    try:
                        entry.workspace_budget(work_fd)
                    except (OSError, ValueError):
                        reason = 'workspace_limit'
                        break
                for key, _ in selector.select(0.05):
                    if key.data == 'stdin':
                        try:
                            count = os.write(key.fd, sending[:16384])
                            sending = sending[count:]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            sending = memoryview(b'')
                        if not sending:
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                        continue
                    try:
                        data = os.read(key.fd, 8192)
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 'stdout' and metadata is None:
                        protocol.extend(data)
                        while b'\n' in protocol and metadata is None:
                            line, tail = protocol.split(b'\n', 1)
                            protocol = bytearray(tail)
                            if len(line) > PROTOCOL_LIMIT:
                                raise files.Refusal('shell entry protocol limit')
                            if evidence is None:
                                evidence = verify_ready(line, unit)
                                os.set_blocking(process.stdin.fileno(), False)
                                selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                            else:
                                value = json.loads(line)
                                if value != {'metadata': {'probe': probe}}:
                                    raise files.Refusal('invalid shell entry metadata')
                                metadata = value['metadata']
                        if metadata is None and len(protocol) > PROTOCOL_LIMIT:
                            raise files.Refusal('shell entry protocol limit')
                        data = bytes(protocol) if metadata is not None else b''
                        if metadata is not None:
                            protocol.clear()
                    if data:
                        target = chunks[key.data]
                        available = OUTPUT_LIMIT - len(target)
                        target.extend(data[:available])
                        if len(data) > available:
                            reason = 'output_limit'
                            break
                if reason is not None:
                    break
            if reason is not None:
                files.stop(unit)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                reason = reason or 'timeout'
                files.stop(unit)
                process.wait(timeout=5)
        if evidence is None:
            raise files.Refusal('shell sandbox never became ready')
        if metadata is None:
            raise files.Refusal('shell entry did not admit the command')
        return {'exit_code': process.returncode, 'reason': reason,
                'stdout': chunks['stdout'].decode('utf-8', errors='replace'),
                'stderr': chunks['stderr'].decode('utf-8', errors='replace'),
                'captured_bytes': {key: len(value) for key, value in chunks.items()},
                'seconds': round(time.monotonic() - start, 4), 'probe': probe,
                'command_sha256': None if probe else hashlib.sha256(request['command'].encode()).hexdigest(),
                'evidence': evidence}
    finally:
        # No lock release or success response until the entire service is settled.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            try:
                if process is not None:
                    # Settle the launcher first, then stop the service. A live
                    # launcher must not create a late unit after stop returns.
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait(timeout=5)
            finally:
                files.stop(unit)
        finally:
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)


def bounded_output(output):
    while len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode()) > TRANSPORT_LIMIT:
        output['ok'] = False
        result = output['result']
        if not result['stdout'] and not result['stderr']:
            raise files.Refusal('shell result metadata exceeds transport limit')
        result['transport_truncated'] = True
        result['reason'] = result['reason'] or 'transport_limit'
        for name in ('stdout', 'stderr'):
            result[name] = result[name][:len(result[name]) // 2]
    return json.dumps(output, ensure_ascii=False, allow_nan=False)


def main():
    if len(sys.argv) not in (3, 4):
        raise files.Refusal('shell requires server-owned workspace, identity and optional compiler prefix')
    root, ident = sys.argv[1:3]
    raw = sys.stdin.buffer.read(entry.REQUEST_LIMIT + 1)
    if len(raw) > entry.REQUEST_LIMIT:
        raise files.Refusal('request limit')
    request = json.loads(raw, object_pairs_hook=entry.unique_object)
    probe = ident == 'probe'
    entry.request_value(request, probe)
    prefix = pins = config_text = None
    if len(sys.argv) == 4:
        prefix, pins, config_text = mojo.toolchain(sys.argv[3])
    with files.locked_workspace(root, ident) as (work_fd, source, unit):
        with tempfile.TemporaryDirectory(prefix='glimmer-shell-') as temporary:
            config = None
            if prefix is not None:
                config = Path(temporary) / 'config'
                config.mkdir(mode=0o700)
                (config / 'modular.cfg').write_text(config_text)
                (config / 'cache').symlink_to('/tmp/cache')
                (config / 'crashdb').symlink_to('/tmp/crashdb')
            result = run(work_fd, source, unit, request, config, prefix, probe)
            try:
                result['workspace'] = entry.workspace_budget(work_fd)
            except (OSError, ValueError):
                result['reason'] = result['reason'] or 'workspace_limit'
            if prefix is not None and mojo.toolchain(str(prefix))[1] != pins:
                raise files.Refusal('compiler identity changed during operation')
    result['compiler'] = None if prefix is None else {'prefix': str(prefix), 'files': pins}
    output = {'ok': result['exit_code'] == 0 and result['reason'] is None,
              'result': result, 'policy': POLICY, 'unit': unit}
    print(bounded_output(output), flush=True)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, files.cancelled)
    signal.signal(signal.SIGINT, files.cancelled)
    try:
        main()
    except (files.Refusal, OSError, ValueError, RecursionError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)[:500], 'unavailable': True, 'policy': POLICY}), flush=True)
        sys.exit(3)
