// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, cleanup } from '@testing-library/react';
import { useAutoMeasure } from './useAutoMeasure';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('useAutoMeasure', () => {
  it('ticks_once_on_mount_with_project_uuid', () => {
    const api = { pty_auto_measure_tick: vi.fn(async () => ({ success: true, started: null, queued: 0 })) };
    renderHook(() => useAutoMeasure(api, 'proj_1'));
    expect(api.pty_auto_measure_tick).toHaveBeenCalledTimes(1);
    expect(api.pty_auto_measure_tick).toHaveBeenCalledWith('proj_1');
  });

  it('does_not_loop_on_rerender_or_new_api_identity', () => {
    const tick = vi.fn(async () => ({ success: true }));
    const { rerender } = renderHook(({ api, uuid }) => useAutoMeasure(api, uuid), {
      initialProps: { api: { pty_auto_measure_tick: tick }, uuid: 'proj_1' }
    });
    for (let i = 0; i < 4; i++) {
      rerender({ api: { pty_auto_measure_tick: tick }, uuid: 'proj_1' });
    }
    expect(tick).toHaveBeenCalledTimes(1);
  });

  it('ticks_again_only_when_project_uuid_changes', () => {
    const tick = vi.fn(async () => ({ success: true }));
    const api = { pty_auto_measure_tick: tick };
    const { rerender } = renderHook(({ uuid }) => useAutoMeasure(api, uuid), {
      initialProps: { uuid: 'proj_1' }
    });
    rerender({ uuid: 'proj_2' });
    expect(tick).toHaveBeenCalledTimes(2);
    expect(tick).toHaveBeenLastCalledWith('proj_2');
  });

  it('does_not_call_without_project_uuid', () => {
    const tick = vi.fn(async () => ({ success: true }));
    renderHook(() => useAutoMeasure({ pty_auto_measure_tick: tick }, ''));
    renderHook(() => useAutoMeasure({ pty_auto_measure_tick: tick }, null));
    expect(tick).not.toHaveBeenCalled();
  });

  it('does_nothing_when_bridge_method_is_missing', () => {
    expect(() => renderHook(() => useAutoMeasure({}, 'proj_1'))).not.toThrow();
    expect(() => renderHook(() => useAutoMeasure(undefined, 'proj_1'))).not.toThrow();
  });

  it('never_throws_on_sync_error_or_rejection', async () => {
    const throwing = { pty_auto_measure_tick: vi.fn(() => { throw new Error('puente caído'); }) };
    expect(() => renderHook(() => useAutoMeasure(throwing, 'proj_1'))).not.toThrow();

    const rejecting = { pty_auto_measure_tick: vi.fn(() => Promise.reject(new Error('fallo'))) };
    expect(() => renderHook(() => useAutoMeasure(rejecting, 'proj_1'))).not.toThrow();
    await Promise.resolve();
    expect(rejecting.pty_auto_measure_tick).toHaveBeenCalledTimes(1);
  });
});
