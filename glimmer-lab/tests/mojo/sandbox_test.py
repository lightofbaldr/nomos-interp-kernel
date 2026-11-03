import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import signal
import hashlib
import socket
import shlex
import shutil
import unittest

ROOT = Path(__file__).resolve().parents[2]
BROKER = ROOT / 'runtime/files/mojo_broker.py'
PREFIX = os.environ.get('GLIMMER_MOJO_PREFIX', os.path.expanduser('~/Projects/nomos-interp-kernel/.pixi/envs/default'))


class MojoSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='glimmer-mojo-test-')
        self.root = Path(self.tmp.name) / 'sandbox'
        self.root.mkdir(mode=0o700)
        self.work = self.root / 'workspaces/1'
        self.work.mkdir(parents=True, mode=0o700)
        self.work.parent.chmod(0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, body, ident='1', prefix=PREFIX, broker=BROKER):
        p = subprocess.run(['/usr/bin/python3', '-I', '-B', str(broker), str(self.root), ident, prefix],
                           input=json.dumps(body), text=True, capture_output=True, timeout=110)
        return p, json.loads(p.stdout)

    def test_real_compiler_build_and_run_are_separate_sandboxes(self):
        data = (ROOT / 'tests/mojo/fixtures/hello.mojo').read_bytes()
        (self.work / 'main.mojo').write_bytes(data)
        p, r = self.call({'path': 'main.mojo'})
        self.assertTrue(r['ok'], r)
        self.assertEqual(p.returncode, 0)
        result = r['result']
        self.assertEqual(result['run']['stdout'].strip(), 'glimmer-mojo-ok')
        for phase in ('compile', 'run'):
            self.assertTrue(all(result[phase]['evidence']['isolation']['checks'].values()))
            self.assertEqual(result[phase]['exit_code'], 0)
            resources = result[phase]['evidence']['resources']
            for key, expected in {'MemoryMax':'4294967296','MemorySwapMax':'0','TasksMax':'128',
                                  'CPUQuotaPerSecUSec':'2s','KillMode':'control-group',
                                  'RuntimeMaxUSec':'1min 32s' if phase=='compile' else '12s'}.items():
                self.assertEqual(resources[key],expected)
        self.assertEqual(result['compile']['phase'], 'compile')
        self.assertEqual(result['run']['phase'], 'run')
        self.assertEqual(list(self.work.iterdir()), [self.work / 'main.mojo'])
        self.assertEqual((self.work / 'main.mojo').read_bytes(), data)

    def test_unknown_arguments_and_missing_compiler_refuse(self):
        for body in ({'path':'main.mojo','flags':['--help']}, {'path':'main.mojo','args':[]}, {'command':'echo nope'}):
            p, r = self.call(body)
            self.assertEqual(p.returncode,3); self.assertTrue(r['unavailable'])
        p, r = self.call({'path':'main.mojo'}, prefix=str(self.root / 'no-compiler'))
        self.assertEqual(p.returncode,3); self.assertTrue(r['unavailable'])

    def test_source_refusals_and_bad_compile_never_execute_a_stale_program(self):
        (self.work / 'bad.mojo').write_text('this is not a Mojo program\n')
        (self.work / 'link.mojo').symlink_to('/etc/passwd')
        for source in ('../secret.mojo', '/etc/passwd', 'link.mojo'):
            p, r = self.call({'path':source})
            self.assertFalse(r['ok']); self.assertIsNone(r.get('result',{}).get('run'))
        (self.work / 'link.mojo').unlink()
        p, r = self.call({'path':'bad.mojo'})
        self.assertFalse(r['ok']); self.assertNotEqual(r['result']['compile']['exit_code'],0)
        self.assertIsNone(r['result']['run']); self.assertTrue(r['result']['compile']['stderr'])

    def test_runtime_failure_output_limit_and_deadline(self):
        cases = [
            ('from std.ffi import external_call\ndef main():\n    external_call["_exit", Int32](Int32(7))\n', 'failure'),
            ('def main():\n    for i in range(100000):\n        print("bounded output here")\n', 'output_limit'),
            ('def main():\n    while True:\n        pass\n', 'timeout'),
        ]
        for code, reason in cases:
            with self.subTest(reason=reason):
                (self.work / 'main.mojo').write_text(code)
                p, r = self.call({'path':'main.mojo'})
                self.assertFalse(r['ok'], r)
                self.assertEqual(r['result']['compile']['exit_code'],0,r)
                ran = r['result']['run']
                self.assertIsNotNone(ran)
                if reason=='failure': self.assertNotEqual(ran['exit_code'],0)
                else: self.assertEqual(ran['reason'],reason,r)
                self.assertLess(len(p.stdout.encode()),65536)

    def test_compiled_code_has_no_home_secrets_gpu_or_host_network_and_cannot_write_source(self):
        secret = self.root.parent / 'host-secret'; secret.write_text('host-only-canary')
        listener = socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
        script = '''import os,socket,json
checks={}
checks['home_empty']=os.listdir('/home')==[]
checks['run_empty']=os.listdir('/run')==[]
checks['no_gpu']=not any(x.startswith('nvidia') for x in os.listdir('/dev'))
checks['no_secret_env']='GLIMMER_HOST_CANARY' not in os.environ
for name,path in [('source_ro','/work/main.mojo'),('config_ro','/config/modular.cfg')]:
    try:
        with open(path,'w') as f: f.write('BAD')
        checks[name]=False
    except OSError: checks[name]=True
try:
    open(SECRET).read();checks['no_host_file']=False
except OSError: checks['no_host_file']=True
s=socket.socket();s.settimeout(1)
try:
    s.connect(('127.0.0.1',PORT));checks['no_host_network']=False
except OSError: checks['no_host_network']=True
print(json.dumps(checks))
'''.replace('SECRET',repr(str(secret))).replace('PORT',str(listener.getsockname()[1]))
        command='/usr/bin/python3 -I -c '+shlex.quote(script)
        code='from std.ffi import external_call\ndef main():\n    var script = String('+json.dumps(command)+')\n    print("child exit", external_call["system", Int32](script.unsafe_ptr()))\n'
        (self.work / 'main.mojo').write_text(code)
        prior = os.environ.get('GLIMMER_HOST_CANARY')
        os.environ['GLIMMER_HOST_CANARY']='test-only-value'
        try:
            _, r = self.call({'path':'main.mojo'})
            self.assertTrue(r['ok'],r)
            checks=json.loads(r['result']['run']['stdout'].splitlines()[0])
            self.assertEqual(set(checks),{'home_empty','run_empty','no_gpu','no_secret_env','source_ro','config_ro','no_host_file','no_host_network'})
            self.assertTrue(all(checks.values()))
            self.assertEqual((self.work / 'main.mojo').read_text(),code)
            self.assertEqual(secret.read_text(),'host-only-canary')
        finally:
            listener.close()
            if prior is None: os.environ.pop('GLIMMER_HOST_CANARY',None)
            else: os.environ['GLIMMER_HOST_CANARY']=prior

    def test_abort_joins_service_and_releases_workspace_lock(self):
        (self.work / 'main.mojo').write_text('def main():\n    while True:\n        pass\n')
        proc = subprocess.Popen(['/usr/bin/python3','-I','-B',str(BROKER),str(self.root),'1',PREFIX],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        proc.stdin.write(json.dumps({'path':'main.mojo'}));proc.stdin.close()
        unit='glimmer-files-'+hashlib.sha256(str(self.root).encode()).hexdigest()[:16]+'-1.service'
        try:
            time.sleep(2)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=15)
            self.assertNotEqual(proc.returncode,0)
            state=subprocess.run(['/usr/bin/systemctl','--user','is-active',unit],capture_output=True,text=True).stdout.strip()
            self.assertNotIn(state,('active','activating','deactivating'))
            (self.work / 'main.mojo').write_bytes((ROOT / 'tests/mojo/fixtures/hello.mojo').read_bytes())
            _, r=self.call({'path':'main.mojo'});self.assertTrue(r['ok'],r)
        finally:
            if proc.poll() is None: proc.kill();proc.wait()
            proc.stdout.close();proc.stderr.close()

    def test_failed_isolation_is_refused_before_the_compiler_is_admitted(self):
        copy = self.root.parent / 'runtime'; copy.mkdir()
        for name in ('broker.py','worker.py','mojo_broker.py','mojo_entry.py'):
            shutil.copyfile(BROKER.with_name(name),copy/name)
        entry = copy/'mojo_entry.py'
        original=entry.read_text(); changed=original.replace("'work_ro': 'ro' in mounts.get('/work', [])", "'work_ro': False")
        self.assertNotEqual(changed,original); entry.write_text(changed)
        (self.work / 'main.mojo').write_bytes((ROOT/'tests/mojo/fixtures/hello.mojo').read_bytes())
        p, r = self.call({'path':'main.mojo'},broker=copy/'mojo_broker.py')
        self.assertEqual(p.returncode,3); self.assertTrue(r['unavailable']); self.assertIn('probe failed',r['error'])


if __name__ == '__main__':
    unittest.main()
