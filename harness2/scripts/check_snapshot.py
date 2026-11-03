# Modified by Light of Baldr LLC, 2026. See MODIFICATIONS.md.
"""Verify the fresh root commit against the read-only source tree, without importing refs."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('HARNESS2_SNAPSHOT_SOURCE', '~/nomos_data/scratch/harness2-read')).expanduser()
REV = '75f8d20ce2e6688624a4d88bf6b305668d6c6a31'

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])

def main():
    roots = git(ROOT, 'rev-list', '--max-parents=0', 'HEAD').decode().splitlines()
    assert len(roots) == 1, 'not a single fresh root'
    source = git(SOURCE, 'ls-tree', '-r', REV).splitlines()
    imported = {line.split(b'\t', 1)[1]: line.split(b'\t', 1)[0]
                for line in git(ROOT, 'ls-tree', '-r', 'snapshot-20261030').splitlines()}
    for line in source:
        spec, name = line.split(b'\t', 1)
        assert imported.get(name) == spec, f'root snapshot differs: {name!r}'
    for line in git(ROOT, 'log', '--all', '--format=%an <%ae>|%cn <%ce>').decode().splitlines():
        assert line == 'Light of Baldr <oss@lightofbaldr.dev>|Light of Baldr <oss@lightofbaldr.dev>'
    print(f'SNAPSHOT VERIFIED: {len(source)} source files, {len(roots)} fresh root')

if __name__ == '__main__':
    main()
