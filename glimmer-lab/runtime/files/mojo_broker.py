"""Fixed build/run commands, each in a separate sandbox service. No shell API.

Only build scratch is writable. Model outputs cannot choose argv, service limits,
compiler root, source workspace, environment, or interpretation of exit status.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broker as files

POLICY = 'glimmer.mojo/bwrap-1'
COMPILE_SECONDS = 90
RUN_SECONDS = 10
OUTPUT_LIMIT = 16384  # bytes per stream, each phase
BUILD_LIMIT = 256 * 1024**2
PROPERTIES = {'MemoryMax': '4294967296', 'MemorySwapMax': '0', 'TasksMax': '128', 'CPUQuota': '200%',
              'LimitFSIZE': '67108864', 'LimitNOFILE': '256', 'TimeoutStopSec': '2', 'KillMode': 'control-group',
              'OOMPolicy': 'stop', 'NoNewPrivileges': 'yes'}


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def toolchain(value):
    root = Path(value)
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise files.Refusal('Mojo prefix must be an existing non-symlink absolute directory')
    for rel in ('bin', 'lib', 'include'):
        p = root / rel
        if not p.is_dir() or p.resolve() != p:
            raise files.Refusal('Mojo runtime directory is missing or symlinked')
    pins = {}
    for rel in ('bin/mojo', 'bin/lld', 'lib/mojo/std.mojoc', 'share/max/modular.cfg'):
        p = root / rel
        if not p.is_file() or p.resolve() != p:
            raise files.Refusal('Mojo identity file is missing or symlinked')
        pins[rel] = sha(p)
    config = (root / 'share/max/modular.cfg').read_text().replace(str(root), '/opt/mojo')
    config = config.replace('/opt/mojo/share/max/.max_cache', '/tmp/cache')
    return root, pins, config


def build_budget(root):
    count = total = 0
    def walk(p, depth):
        nonlocal count, total
        if depth > 32:
            return False
        with os.scandir(p) as scan:
            for ent in scan:
                count += 1
                if count > 8192:
                    return False
                try:
                    s = ent.stat(follow_symlinks=False)
                    if stat.S_ISDIR(s.st_mode):
                        # The compiler/program is untrusted: never follow a raced directory link.
                        fd = os.open(ent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=p)
                        try:
                            if not walk(fd, depth + 1):
                                return False
                        finally:
                            os.close(fd)
                    elif stat.S_ISREG(s.st_mode):
                        total += s.st_size
                except FileNotFoundError:
                    continue
                if total > BUILD_LIMIT:
                    return False
        return True
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return walk(fd, 0)
        finally:
            os.close(fd)
    except OSError:
        return False


def argv(source, scratch, prefix, unit, phase, seconds):
    cmd = ['/usr/bin/systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect', '--service-type=exec', '--unit=' + unit]
    cmd += ['--property=' + k + '=' + v for k, v in {**PROPERTIES, 'RuntimeMaxSec': str(seconds + 2)}.items()]
    cmd += ['--', '/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--disable-userns', '--assert-userns-disabled',
            '--new-session', '--die-with-parent', '--cap-drop', 'ALL', '--clearenv', '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--dir', '/home', '--dir', '/etc', '--dir', '/run',
            '--ro-bind', source, '/work', '--bind', str(scratch / 'build'), '/build',
            '--size', '268435456', '--tmpfs', '/tmp', '--ro-bind', str(scratch / 'config'), '/config']
    for name in ('cc', 'c++', 'cpp'):
        link = Path('/etc/alternatives') / name
        if link.exists():
            target = link.resolve(strict=True)
            if not target.is_relative_to('/usr'):
                raise files.Refusal('system linker outside /usr')
            cmd += ['--symlink', str(target), str(link)]
    for rel in ('bin', 'lib', 'include'):
        cmd += ['--ro-bind', str(prefix / rel), '/opt/mojo/' + rel]
    for rel in ('worker.py', 'mojo_entry.py'):
        cmd += ['--ro-bind', str(Path(__file__).with_name(rel)), '/runtime/' + rel]
    for key, value in {'PATH': '/opt/mojo/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/home', 'TMPDIR': '/tmp',
                       'XDG_CACHE_HOME': '/tmp/cache', 'MODULAR_HOME': '/config'}.items():
        cmd += ['--setenv', key, value]
    return cmd + ['--chdir', '/work', '--remount-ro', '/', '--', '/usr/bin/python3', '-I', '-B', '/runtime/mojo_entry.py', phase]


def verify_ready(line, unit, seconds):
    ready = json.loads(line).get('ready')
    keys = {'root_ro', 'runtime_ro', 'work_ro', 'build_rw', 'compiler_ro', 'config_ro', 'caps_zero', 'no_new_privs',
            'home_empty', 'run_empty', 'cwd', 'env', 'fsize'}
    if not isinstance(ready, dict) or set(ready.get('checks', {})) != keys or any(v is not True for v in ready['checks'].values()):
        raise files.Refusal('Mojo sandbox isolation probe failed')
    for ns in ('net', 'pid'):
        val = ready.get(ns + 'ns')
        if not isinstance(val, str) or not re.fullmatch(ns + r':\[\d+\]', val) or val == os.readlink('/proc/self/ns/' + ns):
            raise files.Refusal('Mojo namespace not isolated')
    expected = {k: v for k, v in PROPERTIES.items() if k not in ('CPUQuota', 'TimeoutStopSec')}
    expected.update(CPUQuotaPerSecUSec='2s', TimeoutStopUSec='2s', RuntimeMaxUSec=f'{seconds + 2}s', ActiveState='active')
    r = files.control('show', unit, '--property=' + ','.join(expected))
    got = dict(row.split('=', 1) for row in r.stdout.decode().splitlines() if '=' in row)
    # systemd prints minutes for the 92-second build bound.
    if seconds + 2 == 92:
        expected['RuntimeMaxUSec'] = '1min 32s'
    if r.returncode or any(got.get(k) != v for k, v in expected.items()):
        raise files.Refusal('Mojo cgroup properties not confirmed')
    return {'isolation': ready, 'resources': got}


def phase_run(source, scratch, prefix, unit, phase, request, seconds):
    files.stop(unit)
    proc = subprocess.Popen(argv(source, scratch, prefix, unit, phase, seconds), env=files.ENV, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
    start = time.monotonic()
    reason = None
    ready = metadata = None
    protocol = bytearray()
    try:
        with selectors.DefaultSelector() as sel:
            for name in chunks:
                stream = getattr(proc, name)
                os.set_blocking(stream.fileno(), False)
                sel.register(stream, selectors.EVENT_READ, name)
            next_scan = start
            while sel.get_map():
                now = time.monotonic()
                if now - start > seconds:
                    reason = 'timeout'; break
                if now >= next_scan:
                    next_scan = now + 0.25
                    if not build_budget(scratch / 'build'):
                        reason = 'build_workspace_limit'; break
                for key, _ in sel.select(0.05):
                    data = os.read(key.fd, 8192)
                    if not data:
                        sel.unregister(key.fileobj); continue
                    if key.data == 'stdout' and metadata is None:
                        protocol.extend(data)
                        if len(protocol) > OUTPUT_LIMIT:
                            raise files.Refusal('Mojo entry protocol limit')
                        while b'\n' in protocol and metadata is None:
                            line, tail = protocol.split(b'\n', 1); protocol = bytearray(tail)
                            if ready is None:
                                ready = verify_ready(line, unit, seconds)
                                proc.stdin.write(json.dumps(request).encode() + b'\n'); proc.stdin.flush(); proc.stdin.close()
                            else:
                                metadata = json.loads(line).get('metadata')
                                if not isinstance(metadata, dict) or not (metadata == {'probe': True} or set(metadata) == {'source_sha256'} and re.fullmatch(r'[0-9a-f]{64}', metadata['source_sha256'])):
                                    raise files.Refusal('invalid source identity')
                        data = bytes(protocol) if metadata is not None else b''
                        if metadata is not None:
                            protocol.clear()
                    if data:
                        target = chunks[key.data]
                        previous = len(target)
                        target.extend(data[:max(0, OUTPUT_LIMIT - len(target))])
                        if previous + len(data) > OUTPUT_LIMIT:
                            reason = 'output_limit'; break
                if reason:
                    break
            if reason:
                files.stop(unit)
            proc.wait(timeout=5)
        if ready is None:
            raise files.Refusal('Mojo sandbox never became ready')
        return {'phase': phase, 'exit_code': proc.returncode, 'reason': reason,
                'stdout': chunks['stdout'].decode('utf-8', errors='replace'), 'stderr': chunks['stderr'].decode('utf-8', errors='replace'),
                'seconds': round(time.monotonic() - start, 4), 'metadata': metadata, 'evidence': ready}
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            files.stop(unit)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        signal.signal(signal.SIGTERM, files.cancelled); signal.signal(signal.SIGINT, files.cancelled)


def main():
    if len(sys.argv) != 4:
        raise files.Refusal('Mojo requires the server-owned workspace and compiler prefix')
    root, ident, prefix_arg = sys.argv[1:]
    request = json.loads(sys.stdin.buffer.read(4097))
    if not isinstance(request, dict) or set(request) != {'path'} or not isinstance(request['path'], str) or len(request['path']) > 1024:
        raise files.Refusal('only path is accepted; no argv, flags, stdin or environment')
    prefix, pins, config = toolchain(prefix_arg)
    with files.locked_workspace(root, ident) as (fd, source, unit):
        files.stop(unit)
        if (os.stat(source).st_dev, os.stat(source).st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
            raise files.Refusal('workspace changed')
        # Trusted infrastructure only; model source is never read/executed on the host.
        with tempfile.TemporaryDirectory(prefix='glimmer-mojo-') as temporary:
            scratch = Path(temporary)
            (scratch / 'build').mkdir(mode=0o700); (scratch / 'config').mkdir(mode=0o700)
            (scratch / 'config/modular.cfg').write_text(config)
            (scratch / 'config/cache').symlink_to('/tmp/cache')
            (scratch / 'config/crashdb').symlink_to('/tmp/crashdb')
            compile_result = phase_run(source, scratch, prefix, unit, 'probe' if ident == 'probe' else 'compile', request, COMPILE_SECONDS)
            success = compile_result['exit_code'] == 0 and compile_result['reason'] is None and compile_result['metadata'] is not None
            run_result = phase_run(source, scratch, prefix, unit, 'run', request, RUN_SECONDS) if success and ident != 'probe' else None
            if run_result is not None:
                success = run_result['exit_code'] == 0 and run_result['reason'] is None and run_result['metadata'] == compile_result['metadata']
            if toolchain(prefix_arg)[1] != pins:
                raise files.Refusal('compiler identity changed during operation')
    result = {'compile': compile_result, 'run': run_result, 'compiler': {'prefix': str(prefix), 'files': pins},
              'workspace': 'read-only; build scratch discarded; not a correctness grader'}
    output = {'ok': success, 'result': result, 'policy': POLICY}
    if len(json.dumps(output, allow_nan=False).encode()) > 60000:
        for part in (compile_result, run_result):
            if part:
                part['stdout'] = part['stdout'][:1000]; part['stderr'] = part['stderr'][:1000]
                part['transport_truncated'] = True
        output['ok'] = False
    print(json.dumps(output, allow_nan=False), flush=True)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, files.cancelled); signal.signal(signal.SIGINT, files.cancelled)
    try:
        main()
    except (files.Refusal, OSError, ValueError, subprocess.SubprocessError) as e:
        print(json.dumps({'ok': False, 'error': str(e)[:500], 'unavailable': True, 'policy': POLICY}), flush=True)
        sys.exit(3)
