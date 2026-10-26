import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";

test("GPU launcher: CPU-only witnesses refuse drift, occupied work, pressure and failed environment resolution", () => {
	const result = execFileSync("python3", ["-B", "-c", String.raw`
import importlib.util
from pathlib import Path
from unittest.mock import patch
import subprocess
spec = importlib.util.spec_from_file_location('launch', 'scripts/e2e-launch.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
route = dict(NOMOS_MODEL='muse', NOMOS_WEIGHT_NVFP4='1', NOMOS_W4A4='1',
    NOMOS_PRECISION_BITS='16', NOMOS_KV_QUANT='1', NOMOS_KV_INT4='0', NOMOS_KV_SWA='0',
    NOMOS_KVSTORE='1', NOMOS_MAX_SEQ='32768', NOMOS_SERVE_SPEC='1', NOMOS_PREFILL_CHUNK='256', SPEC_VB='8')
m.check_route(route)
for key in route:
    try: m.check_route({**route, key: 'wrong'})
    except RuntimeError: pass
    else: raise AssertionError(key)
work = Path.home() / 'nomos_data/readout-spine/pi-e2e-cpu-witness'
with patch.object(Path, 'exists', return_value=True), patch.object(m, 'command') as command:
    try: m.admission(work)
    except RuntimeError: pass
    else: raise AssertionError('adopted an existing run')
    command.assert_not_called()
def command(argv, cwd=m.AGENT, env=None):
    if argv[:3] == ['git', 'status', '--porcelain']: return ''
    if argv[:3] == ['git', 'rev-parse', 'HEAD']: return m.REVISION
    raise subprocess.CalledProcessError(13, argv)
with patch.object(m, 'command', command), patch.object(m, 'sha', return_value=m.LIBRARY_SHA), \
     patch.object(Path, 'exists', return_value=False), patch.object(Path, 'is_symlink', return_value=False), \
     patch.object(Path, 'resolve', lambda p: p), patch.object(Path, 'read_text', return_value='full avg10=25.0'):
    try: m.admission(work)
    except RuntimeError as exc: assert 'PSI' in str(exc)
    else: raise AssertionError('high PSI admitted')
with patch.object(m.subprocess, 'run', side_effect=subprocess.CalledProcessError(13, ['env'])):
    try: m.command(['env'])
    except subprocess.CalledProcessError as exc: assert exc.returncode == 13
    else: raise AssertionError('failed producer appeared successful')
print('CPU LAUNCHER WITNESSES OK')
`], { cwd: new URL("..", import.meta.url), encoding: "utf8" });
	assert.match(result, /CPU LAUNCHER WITNESSES OK/);
});
