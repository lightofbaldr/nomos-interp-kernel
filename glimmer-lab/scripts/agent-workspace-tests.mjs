import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
const types = spawnSync(process.execPath, [
  'node_modules/typescript/bin/tsc', '--ignoreConfig', '--noEmit', '--target', 'es2022',
  '--module', 'nodenext', '--moduleResolution', 'nodenext', '--allowImportingTsExtensions',
  '--skipLibCheck', '--strict', '--types', 'node',
  'src/lib/server/workspace-files.ts', 'src/lib/agent/workspace.ts', 'tests/agent/workspace-files.test.ts'
], {stdio: 'inherit', timeout: 30000});
if (types.error || types.status !== 0) process.exit(types.status || 1);
const result = spawnSync(process.execPath, ['--test', '--test-reporter=tap', 'tests/agent/workspace-files.test.ts'], {stdio: 'inherit', timeout: 120000});
if (result.error || result.status !== 0) process.exit(result.status || 1);
console.log('AGENT WORKSPACE VERIFIED');
