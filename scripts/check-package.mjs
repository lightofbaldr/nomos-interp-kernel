import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {openBridge} from 'glimmer-agent/bridge';
import {ReadoutHost, readoutExtension} from 'glimmer-agent/extension';
import {nomosProvider} from 'glimmer-agent/provider';
import {Harness} from 'glimmer-agent/pi';
import {ProfileHost,profileExtension} from 'glimmer-agent/profile';
import {validateContent} from 'glimmer-agent/profile-model';
import {fileToolsExtension} from 'glimmer-agent/file-tools';
import {TurnCheck} from 'glimmer-agent/contract';
for (const value of [openBridge, ReadoutHost, readoutExtension, nomosProvider, TurnCheck, ProfileHost,profileExtension,validateContent,fileToolsExtension]) assert.equal(typeof value, 'function');
assert.equal(typeof Harness.open, 'function');
for (const name of ['bridge','extension','provider','contract','pi','profile','profile-model','file-tools']) {
  assert.doesNotMatch(await readFile(new URL(`../dist/${name}.js`, import.meta.url), 'utf8'), /from\s+["']\.\/.+\.ts["']/);
}
console.log('AGENT PACKAGE VERIFIED');
