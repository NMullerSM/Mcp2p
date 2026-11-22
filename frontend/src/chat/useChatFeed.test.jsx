// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import { useChatFeed, lastSeenKey, POLL_OPEN_MS, POLL_CLOSED_MS } from './useChatFeed';

const ENTRY = (id, ts, tick = 'delivered') => ({
  id,
  ts_ms: ts,
  source: 'agent',
  sender: { principal: 'agent:a', label: 'Orquestador', kind: 'agent', color: 'c1' },
  recipients: [],
  text: `mensaje ${id}`,
  kind: 'message',
  tick,
  tick_label: 'Entregado'
});

function setVisibility(value) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value });
}

function makeApi(overrides = {}) {
  return {
    chat_get_feed: vi.fn(async () => ({ success: true, entries: [], cursor_ms: 0, sources: [] })),
    ...overrides
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  setVisibility('visible');
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete document.visibilityState;
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('useChatFeed', () => {
  it('sin chat_get_feed en el puente no lee ni lanza', async () => {
    const { result } = renderHook(() => useChatFeed({ api: {}, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 3); });
    expect(result.current.entries).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it('sin proyecto no lee', async () => {
    const api = makeApi();
    renderHook(() => useChatFeed({ api, projectUuid: null, open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 2); });
    expect(api.chat_get_feed).not.toHaveBeenCalled();
  });

  it('la primera lectura usa cursor 0 y las siguientes avanzan con cursor_ms', async () => {
    const api = makeApi({
      chat_get_feed: vi.fn()
        .mockResolvedValueOnce({ success: true, entries: [ENTRY('e1', 100)], cursor_ms: 100, sources: [] })
        .mockResolvedValue({ success: true, entries: [], cursor_ms: 100, sources: [] })
    });
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(api.chat_get_feed.mock.calls[0][0]).toBe('p1');
    expect(api.chat_get_feed.mock.calls[0][1]).toBe(0);
    expect(result.current.entries.map((e) => e.id)).toEqual(['e1']);

    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS); });
    expect(api.chat_get_feed.mock.calls[1][1]).toBe(100);
  });

  it('sondea cada 3 s con el panel abierto y cada 10 s cerrado', async () => {
    const api = makeApi();
    renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const afterMount = api.chat_get_feed.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 2); });
    expect(api.chat_get_feed.mock.calls.length).toBe(afterMount + 2);

    const closed = makeApi();
    renderHook(() => useChatFeed({ api: closed, projectUuid: 'p2', open: false }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const closedAfterMount = closed.chat_get_feed.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 2); });
    expect(closed.chat_get_feed.mock.calls.length).toBe(closedAfterMount);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_CLOSED_MS); });
    expect(closed.chat_get_feed.mock.calls.length).toBeGreaterThan(closedAfterMount);
  });

  it('no sondea con la ventana oculta y lee al volver a mostrarla', async () => {
    const api = makeApi();
    renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const before = api.chat_get_feed.mock.calls.length;

    setVisibility('hidden');
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 3); });
    expect(api.chat_get_feed.mock.calls.length).toBe(before);

    setVisibility('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.chat_get_feed.mock.calls.length).toBe(before + 1);
  });

  it('no solapa llamadas: mientras una sigue en vuelo no se pregunta otra', async () => {
    let resolveFirst;
    const api = makeApi({
      chat_get_feed: vi.fn()
        .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
        .mockResolvedValue({ success: true, entries: [], cursor_ms: 0, sources: [] })
    });
    renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 3); });
    expect(api.chat_get_feed).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst({ success: true, entries: [], cursor_ms: 0, sources: [] });
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS); });
    expect(api.chat_get_feed).toHaveBeenCalledTimes(2);
  });

  it('al cambiar de proyecto reinicia entradas y cursor', async () => {
    const api = makeApi({
      chat_get_feed: vi.fn()
        .mockResolvedValueOnce({ success: true, entries: [ENTRY('p1-e', 50)], cursor_ms: 50, sources: [] })
        .mockResolvedValue({ success: true, entries: [ENTRY('p2-e', 60)], cursor_ms: 60, sources: [] })
    });
    const { result, rerender } = renderHook(({ uuid }) => useChatFeed({ api, projectUuid: uuid, open: true }), {
      initialProps: { uuid: 'p1' }
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.entries.map((e) => e.id)).toEqual(['p1-e']);

    rerender({ uuid: 'p2' });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const lastCall = api.chat_get_feed.mock.calls.at(-1);
    expect(lastCall[0]).toBe('p2');
    expect(lastCall[1]).toBe(0);
    expect(result.current.entries.map((e) => e.id)).toEqual(['p2-e']);
  });

  it('una respuesta de un proyecto anterior no contamina el actual', async () => {
    let resolveOld;
    const api = makeApi({
      chat_get_feed: vi.fn()
        .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
        .mockResolvedValue({ success: true, entries: [ENTRY('nuevo', 70)], cursor_ms: 70, sources: [] })
    });
    const { result, rerender } = renderHook(({ uuid }) => useChatFeed({ api, projectUuid: uuid, open: true }), {
      initialProps: { uuid: 'viejo' }
    });
    rerender({ uuid: 'nuevo' });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => {
      resolveOld({ success: true, entries: [ENTRY('viejo', 10)], cursor_ms: 10, sources: [] });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.entries.map((e) => e.id)).toEqual(['nuevo']);
  });

  it('un fallo de red se muestra como texto y no lanza', async () => {
    const api = makeApi({ chat_get_feed: vi.fn(async () => { throw new Error('puente caido'); }) });
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.error).toBe('puente caido');
    expect(result.current.loading).toBe(false);
  });

  it('una respuesta con success:false muestra su mensaje', async () => {
    const api = makeApi({ chat_get_feed: vi.fn(async () => ({ success: false, code: 'X', message: 'Proyecto no abierto' })) });
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.error).toBe('Proyecto no abierto');
  });

  it('refresh vuelve a leer desde el principio y actualiza el tick de un mensaje', async () => {
    const api = makeApi({
      chat_get_feed: vi.fn()
        .mockResolvedValueOnce({ success: true, entries: [ENTRY('e1', 100, 'queued')], cursor_ms: 100, sources: [] })
        .mockResolvedValueOnce({ success: true, entries: [], cursor_ms: 100, sources: [] })
        .mockResolvedValue({ success: true, entries: [ENTRY('e1', 100, 'read')], cursor_ms: 100, sources: [] })
    });
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_OPEN_MS); });
    await act(async () => { await result.current.refresh(); });
    expect(api.chat_get_feed.mock.calls.at(-1)[1]).toBe(0);
    expect(result.current.entries).toHaveLength(1);
    expect(result.current.entries[0].tick).toBe('read');
  });

  it('expone las fuentes con ok:false', async () => {
    const sources = [{ id: 'node-b', ok: false, reason: 'sin respuesta' }];
    const api = makeApi({ chat_get_feed: vi.fn(async () => ({ success: true, entries: [], cursor_ms: 0, sources })) });
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.sources).toEqual(sources);
  });

  it('lastSeenMs se lee por proyecto de localStorage y markSeen lo persiste', async () => {
    window.localStorage.setItem(lastSeenKey('p1'), '500');
    const api = makeApi();
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    expect(result.current.lastSeenMs).toBe(500);

    act(() => { result.current.markSeen(900); });
    expect(result.current.lastSeenMs).toBe(900);
    expect(window.localStorage.getItem(lastSeenKey('p1'))).toBe('900');

    act(() => { result.current.markSeen(100); });
    expect(result.current.lastSeenMs).toBe(900);
  });

  it('markSeen sin argumento usa el mensaje mas reciente', async () => {
    const api = makeApi({
      chat_get_feed: vi.fn(async () => ({ success: true, entries: [ENTRY('e1', 300), ENTRY('e2', 250)], cursor_ms: 300, sources: [] }))
    });
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    act(() => { result.current.markSeen(); });
    expect(result.current.lastSeenMs).toBe(300);
  });

  it('sin localStorage la lectura y la escritura no rompen el hook', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('bloqueado'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('bloqueado'); });
    const api = makeApi();
    const { result } = renderHook(() => useChatFeed({ api, projectUuid: 'p1', open: true }));
    expect(result.current.lastSeenMs).toBe(0);
    expect(() => act(() => { result.current.markSeen(42); })).not.toThrow();
    expect(result.current.lastSeenMs).toBe(42);
  });
});
