"""Trusted pre-exec entry. No child code runs until the host confirms isolation.

After the handshake this process execs one fixed command. Program output never
shares a trusted JSON result channel; exit status and phase are owned by the host.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import stat
import sys

files = runpy.run_path('/runtime/worker.py')


def main():
    phase = sys.argv[1]
    if phase not in ('compile', 'run', 'probe'):
        raise ValueError('unknown phase')
    status = dict(line.split(':', 1) for line in open('/proc/self/status') if ':' in line)
    mounts = {line.split()[4]: line.split()[5].split(',') for line in open('/proc/self/mountinfo')}
    checks = {
        'root_ro': 'ro' in mounts.get('/', []), 'runtime_ro': 'ro' in mounts.get('/usr', []),
        'work_ro': 'ro' in mounts.get('/work', []), 'build_rw': 'rw' in mounts.get('/build', []),
        'compiler_ro': all('ro' in mounts.get('/opt/mojo/' + p, []) for p in ('bin', 'lib', 'include')),
        'config_ro': 'ro' in mounts.get('/config', []),
        'caps_zero': int(status['CapEff'].strip(), 16) == 0, 'no_new_privs': status['NoNewPrivs'].strip() == '1',
        'home_empty': os.listdir('/home') == [], 'run_empty': os.listdir('/run') == [], 'cwd': os.getcwd() == '/work',
        'env': set(os.environ) <= {'PATH', 'LANG', 'HOME', 'LC_CTYPE', 'PWD', 'TMPDIR', 'XDG_CACHE_HOME', 'MODULAR_HOME'},
        'fsize': resource.getrlimit(resource.RLIMIT_FSIZE) == (67108864, 67108864),
    }
    print(json.dumps({'ready': {'checks': checks, 'netns': os.readlink('/proc/self/ns/net'), 'pidns': os.readlink('/proc/self/ns/pid')}}), flush=True)
    request = json.loads(sys.stdin.buffer.readline(4097))
    if not isinstance(request, dict) or set(request) != {'path'}:
        raise ValueError('only a workspace source path is accepted')
    os.makedirs('/tmp/cache', mode=0o700, exist_ok=True)
    os.makedirs('/tmp/crashdb', mode=0o700, exist_ok=True)
    if phase == 'probe':
        print(json.dumps({'metadata': {'probe': True}}), flush=True)
        os.execv('/opt/mojo/bin/mojo', ['mojo', '--version'])
    root = os.open('/work', files['FLAGS'] | os.O_DIRECTORY)
    try:
        files['inventory'](root)
        names = files['parts'](request['path'])
        if not names[-1].endswith('.mojo'):
            raise ValueError('source must end in .mojo')
        parent = files['directory'](root, names[:-1])
        try:
            source = files['read_at'](parent, names[-1])
        finally:
            os.close(parent)
    finally:
        os.close(root)
    if phase == 'run':
        fd = os.open('/build/program', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > 67108864 or os.read(fd, 4) != b'\x7fELF':
                raise ValueError('compiler did not produce a regular ELF executable')
        finally:
            os.close(fd)
    print(json.dumps({'metadata': {'source_sha256': hashlib.sha256(source.encode()).hexdigest()}}), flush=True)
    # stdin is not an interactive model/host channel.
    devnull = os.open('/dev/null', os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    if phase == 'compile':
        os.execv('/opt/mojo/bin/mojo', ['mojo', 'build', '/work/' + '/'.join(names), '-o', '/build/program'])
    else:
        os.execv('/build/program', ['/build/program'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, UnicodeError) as e:
        print('Mojo source/artifact refused: ' + str(e)[:400], file=sys.stderr)
        sys.exit(64)
