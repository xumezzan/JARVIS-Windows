const { test } = require('node:test');
const assert = require('node:assert/strict');
const { clipboardSample } = require('./clipboard.cjs');
test('disabled observer never touches the native clipboard', async () => {
  assert.equal(await clipboardSample({readText: () => { throw new Error('must not read'); }}, false), '');
});
test('bounded async and legacy synchronous native reads, no truncation or failure leakage', async () => {
  assert.equal(await clipboardSample({readText: async () => 'synthetic'}, true), 'synthetic');
  assert.equal(await clipboardSample({readText: () => 'synchronous'}, true), 'synchronous');
  assert.equal(await clipboardSample({readText: async () => 'x'.repeat(16001)}, true), '');
  assert.equal(await clipboardSample({readText: () => { throw new Error('sensitive native error'); }}, true), '');
});
