"""Trusted pre-exec handshake; workspace code runs only after host admission.

The host may import these validation functions from this trusted runtime file.
Never import a module, source file, or executable from the workspace on the host.
"""
import json
import os
import resource
import stat
import sys

COMMAND_LIMIT = 16384
REQUEST_LIMIT = 131072
FILE_LIMIT = 64 * 1024**2
WORK_LIMIT = 256 * 1024**2
ENTRY_LIMIT = 8192
DEPTH_LIMIT = 32
TMP_LIMIT = 256 * 1024**2
CPU_SECONDS = 90


def request_value(request, probe=False):
    if probe:
        if not isinstance(request, dict) or request:
            raise ValueError('probe accepts only an empty JSON object')
        return None
    if not isinstance(request, dict) or set(request) != {'command'}:
        raise ValueError('only command is accepted; no argv, environment, cwd or limits')
    command = request['command']
    if (not isinstance(command, str) or not command.strip() or '\0' in command
            or len(command.encode('utf-8', errors='strict')) > COMMAND_LIMIT):
        raise ValueError('command must be nonempty UTF-8 text up to 16 KiB, without NUL')
    return command


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def workspace_budget(root):
    """Bounded, no-follow metadata walk; it never reads workspace file contents.

    This is an admission/periodic guard, not a disk quota. In-flight mutations may
    race a walk and cause a conservative refusal. Ancestor directories on the
    host are operator-private and must not be replaced by another host writer.
    """
    count = total = 0

    def walk(fd, depth):
        nonlocal count, total
        if depth > DEPTH_LIMIT:
            raise ValueError('workspace depth limit')
        with os.scandir(fd) as scan:
            for ent in scan:
                count += 1
                if count > ENTRY_LIMIT:
                    raise ValueError('workspace entry limit')
                try:
                    info = ent.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(ent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(child, depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        total += info.st_size
                        if info.st_size > FILE_LIMIT or total > WORK_LIMIT:
                            raise ValueError('workspace byte limit')
                    else:
                        raise ValueError('workspace contains a link or special file')
                except FileNotFoundError:
                    # A deleted entry cannot retain persistent workspace bytes.
                    continue
    walk(root, 0)
    return {'entries': count, 'bytes': total}


def isolation(compiler):
    with open('/proc/self/status') as stream:
        status = dict(line.split(':', 1) for line in stream if ':' in line)
    with open('/proc/self/mountinfo') as stream:
        mounts = {line.split()[4]: line.split()[5].split(',') for line in stream}
    env = {'PATH': '/opt/mojo/bin:/usr/bin:/bin' if compiler else '/usr/bin:/bin',
           'LANG': 'C.UTF-8', 'HOME': '/home', 'PWD': '/work', 'TMPDIR': '/tmp', 'XDG_CACHE_HOME': '/tmp/cache'}
    if compiler:
        env['MODULAR_HOME'] = '/config'
    actual_env = dict(os.environ)
    if actual_env.get('LC_CTYPE') == 'C.UTF-8':
        actual_env.pop('LC_CTYPE')
    tmp = os.statvfs('/tmp')
    checks = {
        'root_ro': 'ro' in mounts.get('/', []), 'runtime_ro': 'ro' in mounts.get('/usr', []),
        'entry_ro': 'ro' in mounts.get('/runtime/shell_entry.py', []),
        'work_rw': 'rw' in mounts.get('/work', []), 'tmp_rw': 'rw' in mounts.get('/tmp', []),
        'proc_ro': 'ro' in mounts.get('/proc', []), 'dev_ro': 'ro' in mounts.get('/dev', []),
        'caps_zero': all(int(status[key].strip(), 16) == 0 for key in ('CapEff', 'CapPrm', 'CapBnd', 'CapAmb')),
        'no_new_privs': status['NoNewPrivs'].strip() == '1',
        'home_empty': os.listdir('/home') == [], 'run_empty': os.listdir('/run') == [],
        'cwd': os.getcwd() == '/work', 'env': actual_env == env,
        'fsize': resource.getrlimit(resource.RLIMIT_FSIZE) == (FILE_LIMIT, FILE_LIMIT),
        'nofile': resource.getrlimit(resource.RLIMIT_NOFILE) == (256, 256),
        'core': resource.getrlimit(resource.RLIMIT_CORE) == (0, 0),
        'cpu': resource.getrlimit(resource.RLIMIT_CPU) == (CPU_SECONDS, CPU_SECONDS),
        'tmp_limit': tmp.f_blocks * tmp.f_frsize == TMP_LIMIT,
        'compiler_ro': (all('ro' in mounts.get('/opt/mojo/' + name, []) for name in ('bin', 'lib', 'include'))
                        if compiler else not os.path.exists('/opt/mojo')),
        'config_ro': ('ro' in mounts.get('/config', [])) if compiler else not os.path.exists('/config'),
    }
    return {'checks': checks, **{ns + 'ns': os.readlink('/proc/self/ns/' + ns)
                                for ns in ('net', 'pid', 'mnt', 'user', 'ipc', 'uts')}}


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('probe', 'execute') or sys.argv[2] not in ('basic', 'mojo'):
        raise ValueError('invalid trusted entry arguments')
    probe, compiler = sys.argv[1] == 'probe', sys.argv[2] == 'mojo'
    root = os.open('/work', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        workspace_budget(root)
    finally:
        os.close(root)
    print(json.dumps({'ready': isolation(compiler)}), flush=True)
    raw = sys.stdin.buffer.readline(REQUEST_LIMIT + 1)
    if len(raw) > REQUEST_LIMIT:
        raise ValueError('request limit')
    command = request_value(json.loads(raw, object_pairs_hook=unique_object), probe)
    os.umask(0o077)
    os.makedirs('/tmp/cache', mode=0o700, exist_ok=True)
    os.makedirs('/tmp/crashdb', mode=0o700, exist_ok=True)
    # The trusted protocol ends before any user command has a chance to print.
    print(json.dumps({'metadata': {'probe': probe}}), flush=True)
    devnull = os.open('/dev/null', os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    if probe:
        # Also exercises the real interpreter and, when configured, compiler.
        command = '/usr/bin/bash --version >/dev/null'
        if compiler:
            command += ' && /opt/mojo/bin/mojo --version'
    os.execv('/usr/bin/bash', ['bash', '--noprofile', '--norc', '-c', command])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, UnicodeError) as error:
        print('Shell admission refused: ' + str(error)[:400], file=sys.stderr)
        sys.exit(64)
