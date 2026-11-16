// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { resetPaneSessionMemory } from './terminalModel';
import { RESIZE_DEBOUNCE_MS } from './TerminalPane';
import {
  FitAddon,
  installResizeObserverDouble,
  makeMatrixApi,
  renderMatrix,
  resetTerminalTestState,
  termOf
} from './terminalTestKit';

// jsdom no renderiza xterm: se sustituye por un doble que registra lo que se le escribe.
vi.mock('@xterm/xterm', () => import('./xtermTestDouble'));
vi.mock('@xterm/addon-fit', () => import('./xtermTestDouble'));

let ro;

beforeEach(() => {
  vi.useFakeTimers();
  ro = installResizeObserverDouble();
});

afterEach(() => {
  cleanup();
  ro.restore();
  vi.useRealTimers();
  resetTerminalTestState();
  resetPaneSessionMemory();
  vi.restoreAllMocks();
});

async function advance(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function openClaude() {
  await advance(1);
  fireEvent.click(screen.getByTestId('cli-open-claude'));
  await advance(1);
  expect(screen.getByTestId('terminal-pane-pane_1')).toBeTruthy();
}

describe('Redimensionado de la terminal (fit + ResizeObserver)', () => {
  it('el debounce es de ~100 ms', () => {
    expect(RESIZE_DEBOUNCE_MS).toBe(100);
  });

  it('al montar ajusta la terminal al panel y avisa al PTY con pty_resize_pane si el tamano difiere', async () => {
    FitAddon.nextDims = { cols: 132, rows: 31 };
    const api = makeMatrixApi();
    renderMatrix(api);
    await openClaude();

    expect(FitAddon.fitCalls).toBeGreaterThan(0);
    expect(termOf('pane_1').cols).toBe(132);
    expect(termOf('pane_1').rows).toBe(31);
    expect(api.pty_resize_pane).toHaveBeenCalledWith('pane_1', 132, 31);
    expect(api.pty_resize_pane).toHaveBeenCalledTimes(1);
    // El tamano real se ve en el slider (ya no hay presets manuales).
    expect(screen.getByTestId('pane-size-pane_1').textContent).toBe('132x31');
  });

  it('si el panel mide lo mismo que el PTY no se llama a pty_resize_pane', async () => {
    FitAddon.nextDims = { cols: 100, rows: 28 }; // = tamano de arranque
    const api = makeMatrixApi();
    renderMatrix(api);
    await openClaude();
    expect(api.pty_resize_pane).not.toHaveBeenCalled();
  });

  it('varios eventos del ResizeObserver seguidos producen UNA sola llamada, pasados ~100 ms', async () => {
    FitAddon.nextDims = { cols: 100, rows: 28 };
    const api = makeMatrixApi();
    renderMatrix(api);
    await openClaude();
    expect(api.pty_resize_pane).not.toHaveBeenCalled();

    // Arrastrar el borde: el tamano cambia en cada evento.
    for (let i = 1; i <= 6; i += 1) {
      FitAddon.nextDims = { cols: 100 + i, rows: 28 + i };
      ro.fire();
      await advance(20); // menos que el debounce
    }
    expect(api.pty_resize_pane).not.toHaveBeenCalled();

    await advance(RESIZE_DEBOUNCE_MS);
    expect(api.pty_resize_pane).toHaveBeenCalledTimes(1);
    expect(api.pty_resize_pane).toHaveBeenCalledWith('pane_1', 106, 34);
  });

  it('un panel oculto (sin medidas) no redimensiona el PTY a un tamano absurdo', async () => {
    FitAddon.nextDims = { cols: 100, rows: 28 };
    const api = makeMatrixApi();
    renderMatrix(api);
    await openClaude();

    FitAddon.nextDims = null; // display:none -> fit no puede medir
    ro.fire();
    await advance(RESIZE_DEBOUNCE_MS + 10);
    FitAddon.nextDims = { cols: 0, rows: 0 };
    ro.fire();
    await advance(RESIZE_DEBOUNCE_MS + 10);
    expect(api.pty_resize_pane).not.toHaveBeenCalled();
    expect(termOf('pane_1').cols).toBe(100);

    // Al volver a ser visible se ajusta de nuevo.
    FitAddon.nextDims = { cols: 90, rows: 25 };
    ro.fire();
    await advance(RESIZE_DEBOUNCE_MS + 10);
    expect(api.pty_resize_pane).toHaveBeenCalledWith('pane_1', 90, 25);
  });

  it('un fallo de pty_resize_pane no rompe la terminal', async () => {
    FitAddon.nextDims = { cols: 100, rows: 28 };
    const api = makeMatrixApi({ pty_resize_pane: vi.fn(async () => { throw new Error('puente caido'); }) });
    renderMatrix(api);
    await openClaude();
    FitAddon.nextDims = { cols: 110, rows: 30 };
    ro.fire();
    await advance(RESIZE_DEBOUNCE_MS + 10);
    expect(api.pty_resize_pane).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('terminal-pane-pane_1')).toBeTruthy();
  });

  it('al cerrar el panel se desconecta el ResizeObserver', async () => {
    FitAddon.nextDims = { cols: 100, rows: 28 };
    const api = makeMatrixApi();
    renderMatrix(api);
    await openClaude();
    expect(ro.observers.some((o) => o.targets.size > 0)).toBe(true);

    fireEvent.click(screen.getByTestId('pane-close-btn-pane_1'));
    await advance(1);
    expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull();
    expect(ro.observers.every((o) => o.targets.size === 0)).toBe(true);
  });
});
