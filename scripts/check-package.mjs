import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {openBridge} from 'glimmer-agent/bridge';
import {ReadoutHost, readoutExtension} from 'glimmer-agent/extension';
import {nomosProvider} from 'glimmer-agent/provider';
import {Harness} from 'glimmer-agent/pi';
import {TurnCheck} from 'glimmer-agent/contract';
for (const value of [openBridge, ReadoutHost, readoutExtension, nomosProvider, TurnCheck]) assert.equal(typeof value, 'function');
assert.equal(typeof Harness.open, 'function');
for (const name of ['bridge','extension','provider','contract','pi']) {
  assert.doesNotMatch(await readFile(new URL(`../dist/${name}.js`, import.meta.url), 'utf8'), /from\s+["']\.\/.+\.ts["']/);
}
console.log('AGENT PACKAGE VERIFIED');
