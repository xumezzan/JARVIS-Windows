const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { runtimeConfiguration } = require('./runtime.cjs');
const { cleanEnvironment } = require('./environment.cjs');

test('official electron.exe uses its installed slot even when isPackaged is false', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-runtime-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const config = { root: path.join(directory, 'slot'), python: path.join(directory, 'slot', 'python.exe') };
  fs.writeFileSync(path.join(directory, 'python-runtime.json'), JSON.stringify(config));
  assert.deepEqual(runtimeConfiguration('unused-dev-root', directory, false), config);
  assert.deepEqual(runtimeConfiguration('unused-dev-root', directory, true), config);
});

test('an installed runtime never falls back after a missing or invalid configuration', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-runtime-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  assert.throws(() => runtimeConfiguration(directory, directory, true));
  for (const value of ['null', '{}', JSON.stringify({ root: directory, python: 'relative.exe' }),
    JSON.stringify({ root: directory, python: path.join(directory, 'python.exe'), extra: true }),
    ' '.repeat(8193)]) {
    fs.writeFileSync(path.join(directory, 'python-runtime.json'), value);
    assert.throws(() => runtimeConfiguration(directory, directory, false));
  }
});

test('slot browser cache reaches the backend without forwarding provider secrets', () => {
  assert.deepEqual(cleanEnvironment({ PLAYWRIGHT_BROWSERS_PATH: 'slot/browsers', OPENAI_API_KEY: 'synthetic' }),
    { PLAYWRIGHT_BROWSERS_PATH: 'slot/browsers' });
});
