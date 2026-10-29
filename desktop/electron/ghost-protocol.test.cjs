const {test} = require('node:test');
const assert = require('node:assert/strict');
const {Runtime} = require('./runtime.cjs');

function runtime() {
  const instance = Object.create(Runtime.prototype);
  const messages = [];
  instance.buffer = Buffer.alloc(0);
  instance.pending = new Map();
  instance.state = {ready: true};
  instance.failure = '';
  instance.child = {stdin: {end() {instance.ended = true;}}};
  instance.notify = message => messages.push(message);
  return {instance, messages};
}

test('yield and idle events preserve the runtime and the following task response', () => {
  const {instance, messages} = runtime();
  instance.read(Buffer.from('{"event":"cursor_state","cursor_state":"yielding"}\n'));
  instance.read(Buffer.from('{"event":"cursor_state","cursor_state":"idle"}\n'));
  instance.read(Buffer.from('{"event":"state","state":{"ready":true,"task":{"state":"finished"}}}\n'));
  let received;
  instance.pending.set('second', {resolve: value => {received = value;}});
  instance.read(Buffer.from('{"id":"second","ok":true,"state":{"ready":true}}\n'));
  assert.equal(instance.failure, '');
  assert.equal(instance.ended, undefined);
  assert.deepEqual(messages.slice(0,2), [
    {event:'cursor_state', cursor_state:'yielding'},
    {event:'cursor_state', cursor_state:'idle'},
  ]);
  assert.deepEqual(received, {ready:true});
});

test('malformed cursor states fail the private protocol closed', () => {
  for (const value of [null, '', 'approve', {}, ['idle']]) {
    const {instance} = runtime();
    instance.read(Buffer.from(JSON.stringify({event:'cursor_state', cursor_state:value})+'\n'));
    assert.equal(instance.failure, 'runtime_protocol_failed');
    assert.equal(instance.ended, true);
  }
});
