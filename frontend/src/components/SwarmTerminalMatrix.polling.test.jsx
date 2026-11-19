// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { resetPaneSessionMemory } from './terminals/terminalModel';
import {
  makeMatrixApi,
  renderMatrix,
  resetTerminalTestState,
  termOf
} from './terminals/terminalTestKit';

// jsdom no renderiza xterm: se sustituye por un doble que registra lo que se le escribe.
vi.mock('@xterm/xterm', () => import('./terminals/xtermTestDouble'));
vi.mock('@xterm/addon-fit', () => import('./terminals/xtermTestDouble'));

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  resetTerminalTestState();
  resetPaneSessionMemory();
  vi.restoreAllMocks();
});

/** Deja correr promesas pendientes y avanza el reloj simulado `ms` milisegundos. */
async function advance(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function openClaude() {
  await advance(1); // catalogo y estado inicial
  fireEvent.click(screen.getByTestId('cli-open-claude'));
  await advance(1);
  expect(screen.getByTestId('terminal-pane-pane_1')).toBeTruthy();
}

/** API cuyo sondeo responde con salida solo en la primera llamada y registra la hora de cada llamada. */
function recordingApi(over = {}) {
  const times = [];
  const api = makeMatrixApi({
    pty_poll_output: vi.fn(async (paneId, since = 0) => {
      times.push(Date.now());
      return {
        success: true, alive: true, paused: false, rss_mb: 10, since_seq: since,
        next_seq: 1, truncated: false, text: times.length === 1 ? 'hola\n' : ''
      };
    }),
    ...over
  });
  return { api, times };
}

const deltas = (times) => times.slice(1).map((t, i) => t - times[i]);

describe('Sondeo adaptativo de la salida', () => {
  it('va a ~30 ms con salida reciente y retrocede a ~300 ms en reposo', async () => {
    const { api, times } = recordingApi();
    renderMatrix(api);
    await openClaude();

    await advance(8000);
    const d = deltas(times);
    expect(d[0]).toBe(30); // tras recibir salida
    expect(d.slice(0, 40).every((x) => x === 30)).toBe(true); // ~1.5 s rapido
    expect(d).toContain(100); // escalon intermedio
    expect(d[d.length - 1]).toBe(300); // reposo
    expect(d.slice(-5).every((x) => x === 300)).toBe(true);
    // Reposo = unas 3 llamadas por segundo por panel, no 30.
    const lastSecond = times.filter((t) => t > Date.now() - 1000);
    expect(lastSecond.length).toBeLessThanOrEqual(4);
  });

  it('teclear adelanta el siguiente sondeo aunque el bucle estuviera en reposo', async () => {
    const { api, times } = recordingApi();
    renderMatrix(api);
    await openClaude();
    await advance(8000); // bien en reposo (300 ms)
    // Sincroniza con el reloj: justo despues de un sondeo, el siguiente tocaria en ~300 ms.
    const seen = times.length;
    while (times.length === seen) await advance(1);
    await advance(10);
    const lastPoll = times[times.length - 1];

    act(() => termOf('pane_1').emitData('x'));
    const typedAt = Date.now();
    expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', 'x', true);
    await advance(40);
    const next = times.find((t) => t > lastPoll);
    expect(next).toBeGreaterThanOrEqual(typedAt);
    expect(next - typedAt).toBeLessThanOrEqual(30);
    expect(next - lastPoll).toBeLessThan(100);
  });

  it('teclear muy deprisa no deja al bucle sin sondear (nunca se retrasa el sondeo)', async () => {
    const { api, times } = recordingApi();
    renderMatrix(api);
    await openClaude();
    await advance(8000);
    const countBefore = times.length;
    for (let i = 0; i < 20; i += 1) {
      act(() => termOf('pane_1').emitData('a'));
      await advance(10); // una tecla cada 10 ms durante 200 ms
    }
    expect(times.length - countBefore).toBeGreaterThanOrEqual(5);
  });

  it('con la vista oculta sondea a ~1 s', async () => {
    const { api, times } = recordingApi();
    const { rerenderMatrix } = renderMatrix(api);
    await openClaude();
    await advance(8000);

    rerenderMatrix({ active: false });
    await advance(500); // deja agotar el temporizador ya programado
    const mark = times.length;
    await advance(6000);
    const hiddenDeltas = deltas(times.slice(mark - 1));
    expect(hiddenDeltas.length).toBeGreaterThanOrEqual(5);
    expect(hiddenDeltas.every((x) => x === 1000)).toBe(true);
  });

  it('nunca solapa llamadas: espera la respuesta antes de pedir la siguiente', async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async () => {
        await gate;
        return { success: true, alive: true, paused: false, rss_mb: 10, next_seq: 1, truncated: false, text: '' };
      })
    });
    renderMatrix(api);
    await openClaude();

    await advance(5000);
    expect(api.pty_poll_output).toHaveBeenCalledTimes(1); // colgada: no se acumulan llamadas
    release();
    await advance(1);
    await advance(400);
    expect(api.pty_poll_output.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('un solo bucle por panel aunque cambien otros paneles o el estado', async () => {
    const times = { pane_1: [], pane_2: [] };
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async (paneId, since = 0) => {
        times[paneId].push(Date.now());
        return { success: true, alive: true, paused: false, rss_mb: 10, since_seq: since, next_seq: 1, truncated: false, text: '' };
      })
    });
    renderMatrix(api);
    await openClaude();
    fireEvent.click(screen.getByTestId('cli-open-codex'));
    await advance(1);
    await advance(2000);

    // Sin repeticiones dentro de un mismo instante: un sondeo por turno de cada panel.
    ['pane_1', 'pane_2'].forEach((id) => {
      const d = deltas(times[id]);
      expect(d.length).toBeGreaterThan(3);
      expect(d.every((x) => x >= 30)).toBe(true);
    });
  });
});
