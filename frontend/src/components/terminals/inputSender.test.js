import { describe, it, expect, vi } from 'vitest';
import { createInputSender } from './inputSender';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe('createInputSender (EGM-80)', () => {
  it('la primera tecla sale en el mismo turno y nunca hay dos llamadas en vuelo', async () => {
    const calls = [];
    const gates = [];
    const send = vi.fn((chunk) => {
      calls.push(chunk);
      const d = deferred();
      gates.push(d);
      return d.promise;
    });
    const sender = createInputSender({ send });

    sender.push('a');
    expect(calls).toEqual(['a']); // sincrono
    sender.push('b');
    sender.push('c');
    expect(calls).toEqual(['a']); // 'b' y 'c' esperan a que termine 'a'

    gates[0].resolve({ success: true });
    await Promise.resolve();
    await Promise.resolve();
    expect(calls).toEqual(['a', 'bc']); // agrupadas y en orden

    sender.push('d');
    expect(calls).toEqual(['a', 'bc']);
    gates[1].resolve({ success: true });
    await vi.waitFor(() => expect(calls).toEqual(['a', 'bc', 'd']));
    gates[2].resolve({ success: true });
    await vi.waitFor(() => expect(sender.isBusy()).toBe(false));
  });

  it('con respuestas desordenadas del puente el PTY recibe el texto exacto', async () => {
    const written = [];
    const send = vi.fn(async (chunk) => {
      await new Promise((r) => setTimeout(r, Math.random() * 3));
      written.push(chunk);
      return { success: true };
    });
    const sender = createInputSender({ send });
    const text = 'echo abcdefghijklmnopqrstuvwxyz0123456789 > /tmp/x\r';
    let last;
    for (const ch of text) last = sender.push(ch);
    await last;
    await vi.waitFor(() => expect(sender.isBusy()).toBe(false));
    expect(written.join('')).toBe(text);
    expect(send.mock.calls.length).toBeLessThan(text.length);
  });

  it('un fallo descarta lo pendiente y avisa; la siguiente tecla vuelve a intentarlo', async () => {
    const onResult = vi.fn();
    const onError = vi.fn();
    const d = deferred();
    const send = vi.fn()
      .mockImplementationOnce(() => d.promise)
      .mockImplementation(async () => ({ success: true }));
    const sender = createInputSender({ send, onResult, onError });

    const p = sender.push('x');
    sender.push('y');
    d.resolve({ success: false, code: 'SESSION_NOT_ALIVE', message: 'fin' });
    await p;
    expect(send).toHaveBeenCalledTimes(1); // 'y' no se escribe en un panel caido
    expect(onResult).toHaveBeenCalledWith(expect.objectContaining({ code: 'SESSION_NOT_ALIVE' }));

    await sender.push('z');
    expect(send).toHaveBeenLastCalledWith('z');
  });

  it('una excepcion del puente llega a onError y no bloquea el envio', async () => {
    const onError = vi.fn();
    const send = vi.fn()
      .mockImplementationOnce(async () => { throw new Error('puente caido'); })
      .mockImplementation(async () => ({ success: true }));
    const sender = createInputSender({ send, onError });
    await sender.push('a');
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
    await sender.push('b');
    expect(send).toHaveBeenLastCalledWith('b');
    expect(sender.isBusy()).toBe(false);
  });

  it('clear() descarta lo pendiente (cierre del panel)', async () => {
    const d = deferred();
    const send = vi.fn()
      .mockImplementationOnce(() => d.promise)
      .mockImplementation(async () => ({ success: true }));
    const sender = createInputSender({ send });
    const p = sender.push('a');
    sender.push('b');
    sender.clear();
    d.resolve({ success: true });
    await p;
    expect(send).toHaveBeenCalledTimes(1);
  });
});
