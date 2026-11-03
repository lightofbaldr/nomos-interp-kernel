"""Bounded local Task 6 launcher; never starts/stops the production serve itself.

Invoke inside the reviewed with_serve_down wrapper and a separate systemd user unit.
--check validates admission without creating a directory or loading the engine.

Historical: it pins the kernel revision and library its run verified (REVISION and LIBRARY_SHA below), so it
refuses any other kernel build by design. Don't relax that gate to make it pass on a newer kernel.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.request

AGENT = Path(__file__).resolve().parent.parent
KERNEL = Path(os.environ.get('NOMOS_KERNEL_DIR', Path.home() / 'Projects/nomos-interp-kernel')).resolve()
REVISION = '5dcc2e403deca0a42273bf2cfa9bee62814b6146'
LIBRARY = KERNEL / 'libnomos_kernel-muse.so'
LIBRARY_SHA = 'c660097f7bec116210c06ed67e83bc07139b8eea3da850cec7d45d9a1f0628f9'
PRESSURE = Path(f'/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/memory.pressure')


def sha(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(argv, cwd=AGENT, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, check=True, text=True, capture_output=True).stdout


def check_route(env):
    expected = {'NOMOS_MODEL': 'muse', 'NOMOS_WEIGHT_NVFP4': '1', 'NOMOS_W4A4': '1',
                'NOMOS_PRECISION_BITS': '16', 'NOMOS_KV_QUANT': '1', 'NOMOS_KV_INT4': '0',
                'NOMOS_KV_SWA': '0', 'NOMOS_KVSTORE': '1', 'NOMOS_MAX_SEQ': '32768',
                'NOMOS_SERVE_SPEC': '1', 'NOMOS_PREFILL_CHUNK': '256', 'SPEC_VB': '8'}
    for name, value in expected.items():
        if env.get(name) != value:
            raise RuntimeError(f'resolved route mismatch: {name} must be {value}')


def admission(work):
    if not work.is_absolute() or work.parent != Path.home() / 'nomos_data/readout-spine':
        raise RuntimeError('--work must be a fresh immediate child of ~/nomos_data/readout-spine')
    if work.exists() or work.is_symlink() or not work.name.startswith('pi-e2e-'):
        raise RuntimeError('work directory must be absent and named pi-e2e-*')
    if work.parent.resolve() != work.parent:
        raise RuntimeError('work parent must not traverse symlinks')
    for repo in (AGENT, KERNEL):
        if command(['git', 'status', '--porcelain'], cwd=repo).strip():
            raise RuntimeError(f'checkout is not clean: {repo}')
    if command(['git', 'rev-parse', 'HEAD'], cwd=KERNEL).strip() != REVISION or sha(LIBRARY) != LIBRARY_SHA:
        raise RuntimeError('kernel identity changed')
    pressure = PRESSURE.read_text()
    match = re.search(r'^full avg10=([\d.]+)', pressure, re.M)
    if match is None or not float(match[1]) < 10:
        raise RuntimeError('PSI admission refused (full avg10 must be below 10%)')
    if shutil.disk_usage('/tmp').free < 2 * 1024**3:
        raise RuntimeError('/tmp admission refused (need at least 2 GiB free)')
    with socket.socket() as port:
        port.bind(('127.0.0.1', 8191))  # refuse an occupied port before any engine work
    # Status is checked BEFORE parsing; a failed producer can never appear as an empty successful environment.
    base = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    resolved = command([str(KERNEL / 'pyhost/serve_nomos.sh'), '--model', 'muse', 'env'], KERNEL, base)
    env = dict(re.findall(r'^([A-Z][A-Z0-9_]*)=(.*)$', resolved, re.M))
    env.pop('SPEC_ENV', None)  # a shell fragment, not an environment setting
    check_route(env)
    env.update(SO_PATH=str(LIBRARY), NOMOS_ENGINE_SOCKET=str(work / 'engine.sock'),
               NOMOS_KVSTORE_DIR=str(work / 'kvstore'), NOMOS_PORT='8191', NOMOS_HOST='127.0.0.1',
               CUBLAS_WORKSPACE_CONFIG=':4096:8', CUDA_VISIBLE_DEVICES='0', PYTHONDONTWRITEBYTECODE='1')
    return env, {'kernel_commit': REVISION, 'library_sha256': LIBRARY_SHA,
                 'agent_commit': command(['git', 'rev-parse', 'HEAD']).strip(),
                 'launcher_sha256': sha(__file__), 'shell_sha256': sha(AGENT / 'scripts/e2e-gpu.sh'),
                 'conversation_sha256': sha(AGENT / 'scripts/e2e-gpu.ts'),
                 'kernel_launcher_sha256': sha(KERNEL / 'pyhost/serve_nomos.sh'),
                 'registry_sha256': sha(Path.home() / 'nomos_data/readout-assets/registry.json'),
                 'node': command(['node', '--version']).strip(), 'env': env,
                 'pressure_before': pressure, 'tmp_free_bytes': shutil.disk_usage('/tmp').free}


def stop(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)


def wait_ready(proc, ready, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'child exited before readiness: {proc.returncode}')
        if ready():
            return
        time.sleep(1)
    raise RuntimeError('child readiness timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    env, record = admission(args.work)
    if args.check:
        print(json.dumps(record, indent=2))
        return 0
    args.work.mkdir()  # exclusive ownership; never adopts or removes a previous run
    (args.work / 'launch.json').write_text(json.dumps(record, indent=2) + '\n')
    children, logs = [], []
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        effective = {**os.environ, **env}
        for name, source in [('daemon', 'engine_daemon.py'), ('serve', 'serve.py')]:
            log = open(args.work / f'{name}.log', 'x')
            logs.append(log)
            child = subprocess.Popen(['pixi', 'run', '--no-install', 'python3', f'pyhost/{source}'],
                                     cwd=KERNEL, env=effective, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            children.append(child)
            if name == 'daemon':
                wait_ready(child, lambda: '[daemon] READY' in (args.work / 'daemon.log').read_text(), 240)
            else:
                def health():
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:8191/health', timeout=3) as response:
                            data = json.load(response)
                    except (OSError, ValueError):
                        return False
                    if not (data.get('ready') is True and data.get('spec') is True
                            and data.get('kvstore', {}).get('status') == 'open'
                            and data.get('readout', {}).get('supported') is True):
                        return False
                    (args.work / 'health.json').write_text(json.dumps(data, indent=2) + '\n')
                    return True
                wait_ready(child, health, 90)
        # One Pi process, separate from the kernel. Its exact exit is the stage's exit.
        node = subprocess.Popen(['node', str(AGENT / 'scripts/e2e-gpu.ts'),
                                 'http://127.0.0.1:8191/v1', str(args.work)],
                                cwd=AGENT, env=effective, start_new_session=True)
        children.append(node)
        rc = node.wait(timeout=480)
        if sha(LIBRARY) != LIBRARY_SHA or command(['git', 'rev-parse', 'HEAD'], KERNEL).strip() != REVISION:
            raise RuntimeError('kernel identity changed during the run')
        (args.work / 'EXIT').write_text(f'EXIT {rc}\n')
        return rc
    finally:
        for child in reversed(children):
            stop(child)
        for log in logs:
            log.close()


if __name__ == '__main__':
    raise SystemExit(main())
