import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPanePoller } from './panePoller';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

describe('createPanePoller', () => {
  it('la primera llamada sale ya y las siguientes esperan getDelay() tras cada respuesta', async () => {
    const poll = vi.fn(async () => ({ ok: true }));
    const poller = createPanePoller({ poll, onResult: () => true, getDelay: () => 50 });
    poller.start();
    expect(poll).toHaveBeenCalledTimes(1); // sincrona: sin esperar un turno del temporizador

    await vi.advanceTimersByTimeAsync(49);
    expect(poll).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(poll).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it('nunca hay dos llamadas en vuelo: una respuesta lenta bloquea la siguiente', async () => {
    const gate = deferred();
    const poll = vi.fn(() => gate.promise);
    const poller = createPanePoller({ poll, onResult: () => true, getDelay: () => 30 });
    poller.start();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(poller.isInFlight()).toBe(true);

    poller.wake(); // tampoco wake() puede solapar una llamada en vuelo
    await vi.advanceTimersByTimeAsync(100);
    expect(poll).toHaveBeenCalledTimes(1);

    gate.resolve({ ok: true });
    await vi.advanceTimersByTimeAsync(30);
    expect(poll).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it('el retardo se reevalua en cada vuelta (adaptativo)', async () => {
    const delays = [30, 30, 300, 1000];
    const times = [];
    const poller = createPanePoller({
      poll: async () => { times.push(Date.now()); return {}; },
      onResult: () => true,
      getDelay: () => delays.shift() ?? 1000
    });
    poller.start();
    await vi.advanceTimersByTimeAsync(5000);
    const gaps = times.slice(1, 5).map((t, i) => t - times[i]);
    expect(gaps).toEqual([30, 30, 300, 1000]);
    poller.stop();
  });

  it('wake() adelanta un sondeo lento pero nunca retrasa uno que ya toca antes', async () => {
    let delay = 300;
    const poll = vi.fn(async () => ({}));
    const poller = createPanePoller({ poll, onResult: () => true, getDelay: () => delay });
    poller.start();
    await vi.advanceTimersByTimeAsync(1); // 1.a respuesta; el siguiente toca en 300 ms
    expect(poll).toHaveBeenCalledTimes(1);

    delay = 30;
    poller.wake();
    await vi.advanceTimersByTimeAsync(30);
    expect(poll).toHaveBeenCalledTimes(2);

    // Con un sondeo ya cercano (toca en 30 ms), wake() con un retardo mayor no lo retrasa.
    await vi.advanceTimersByTimeAsync(10);
    delay = 500;
    poller.wake();
    await vi.advanceTimersByTimeAsync(25);
    expect(poll).toHaveBeenCalledTimes(3);
    poller.stop();
  });

  it('onResult devuelve false -> el bucle se detiene solo', async () => {
    const poll = vi.fn(async () => ({ alive: false }));
    const poller = createPanePoller({ poll, onResult: (r) => r.alive, getDelay: () => 30 });
    poller.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(poller.isRunning()).toBe(false);
  });

  it('un fallo de la llamada no rompe el ciclo', async () => {
    const poll = vi.fn()
      .mockRejectedValueOnce(new Error('puente caido'))
      .mockResolvedValue({ ok: true });
    const onResult = vi.fn(() => true);
    const poller = createPanePoller({ poll, onResult, getDelay: () => 30 });
    poller.start();
    await vi.advanceTimersByTimeAsync(100);
    expect(poll.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(onResult).toHaveBeenCalled();
    poller.stop();
  });

  it('stop() cancela lo pendiente; la respuesta ya en vuelo se entrega pero no se programa otra', async () => {
    const gate = deferred();
    const poll = vi.fn(() => gate.promise);
    const onResult = vi.fn(() => true);
    const poller = createPanePoller({ poll, onResult, getDelay: () => 30 });
    poller.start();
    poller.stop();
    gate.resolve({ ok: true });
    await vi.advanceTimersByTimeAsync(500);
    expect(onResult).toHaveBeenCalledTimes(1);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(poller.isRunning()).toBe(false);
  });
});
