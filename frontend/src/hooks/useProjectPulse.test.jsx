// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useProjectPulse } from './useProjectPulse';

function makeApi(fingerprints) {
  let i = 0;
  return {
    get_project_sync_pulse: vi.fn(async () => {
      const fp = fingerprints[Math.min(i, fingerprints.length - 1)];
      i += 1;
      return { success: true, fingerprint: fp, cards_updated_at: fp, a2a_updated_at: fp };
    }),
  };
}

describe('useProjectPulse', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('no re-arma el intervalo cuando cambian las identidades de los callbacks', () => {
    const api = makeApi(['a']);
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    const { rerender } = renderHook(
      (props) => useProjectPulse({ api, projectUuid: 'p1', intervalMs: 1000, ...props }),
      { initialProps: { onCardsChange: () => {}, onConsensusChange: () => {}, onGitChange: () => {} } }
    );
    for (let k = 0; k < 5; k++) {
      rerender({ onCardsChange: () => {}, onConsensusChange: () => {}, onGitChange: () => {} });
    }
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
  });

  it('invoca siempre la versión más reciente del callback', async () => {
    const api = makeApi(['a', 'b']);
    const oldCb = vi.fn();
    const newCb = vi.fn();
    const { rerender } = renderHook(
      (props) => useProjectPulse({ api, projectUuid: 'p1', intervalMs: 1000, ...props }),
      { initialProps: { onCardsChange: oldCb } }
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    rerender({ onCardsChange: newCb });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(newCb).toHaveBeenCalledTimes(1);
    expect(oldCb).not.toHaveBeenCalled();
  });

  it('no solapa llamadas IPC si la anterior sigue en vuelo', async () => {
    const api = { get_project_sync_pulse: vi.fn(() => new Promise(() => {})) };
    renderHook(() => useProjectPulse({ api, projectUuid: 'p1', intervalMs: 1000 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(api.get_project_sync_pulse).toHaveBeenCalledTimes(1);
  });
});
