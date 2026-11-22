// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, renderHook, act } from '@testing-library/react';
import React from 'react';
import { usePermissionsTab } from './usePermissionsTab';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const ITEM = (id) => ({
  msg_id: id,
  reason: 'CHAIN_DEPTH_EXCEEDED',
  from: { kind: 'pane', agent_name: 'a', pane_id: 'pane_1' },
  to: { kind: 'pane', agent_name: 'b', pane_id: 'pane_2' },
  kind: 'note',
  chain: { hop: 1, max_depth: 1 },
  trust: 'pane_authenticated',
  text: 'x'
});

function makeApi(items = []) {
  return {
    relay_list_human_inbox: vi.fn(async () => ({ success: true, items })),
    relay_audit_status: vi.fn(async () => ({ success: true, chain: { ok: true, rows: 0 }, messages: {}, global_paused: false, paused_panes: [] }))
  };
}

describe('usePermissionsTab', () => {
  it('sin proyecto activo devuelve [] y no sondea el relevo', async () => {
    const api = makeApi([ITEM('m-1')]);
    const { result } = renderHook(() => usePermissionsTab(api, null));
    expect(result.current).toEqual([]);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.relay_list_human_inbox).not.toHaveBeenCalled();
    expect(api.relay_audit_status).not.toHaveBeenCalled();
  });

  it('sin metodos del relevo en el puente devuelve []', () => {
    const { result } = renderHook(() => usePermissionsTab({}, 'proj_1'));
    expect(result.current).toEqual([]);
  });

  it('con proyecto y relevo devuelve una pestaña «Permisos» cuya insignia es el numero de retenidos', async () => {
    const api = makeApi([ITEM('m-1'), ITEM('m-2')]);
    const { result } = renderHook(() => usePermissionsTab(api, 'proj_1'));
    await waitFor(() => expect(result.current[0]?.badge).toBe(2));
    expect(result.current).toHaveLength(1);
    expect(result.current[0]).toMatchObject({ id: 'permisos', label: 'Permisos', badge: 2 });
    expect(typeof result.current[0].render).toBe('function');
    expect(api.relay_list_human_inbox).toHaveBeenCalledWith('proj_1');
  });

  it('sin retenidos la insignia es 0', async () => {
    const api = makeApi([]);
    const { result } = renderHook(() => usePermissionsTab(api, 'proj_1'));
    await waitFor(() => expect(api.relay_list_human_inbox).toHaveBeenCalled());
    await waitFor(() => expect(result.current[0]?.badge).toBe(0));
  });

  it('el array se mantiene estable entre renders si nada cambia', async () => {
    const api = makeApi([ITEM('m-1')]);
    const { result, rerender } = renderHook(({ projectUuid }) => usePermissionsTab(api, projectUuid), {
      initialProps: { projectUuid: 'proj_1' }
    });
    await waitFor(() => expect(result.current[0]?.badge).toBe(1));
    const first = result.current;
    rerender({ projectUuid: 'proj_1' });
    rerender({ projectUuid: 'proj_1' });
    expect(result.current).toBe(first);
  });

  it('el array cambia cuando cambia la insignia (el sondeo trae mas retenidos)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let items = [ITEM('m-1')];
    const api = {
      relay_list_human_inbox: vi.fn(async () => ({ success: true, items })),
      relay_audit_status: vi.fn(async () => ({ success: true, chain: { ok: true }, messages: {}, global_paused: false, paused_panes: [] }))
    };
    try {
      const { result } = renderHook(() => usePermissionsTab(api, 'proj_1'));
      await waitFor(() => expect(result.current[0]?.badge).toBe(1));
      const before = result.current;
      items = [ITEM('m-1'), ITEM('m-2'), ITEM('m-3')];
      // El sondeo de useRelayState es cada 5 s: se avanza el reloj hasta el siguiente ciclo.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5100);
      });
      await waitFor(() => expect(result.current[0]?.badge).toBe(3));
      expect(result.current).not.toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it('render() monta la pestaña con sus datos: la lista de retenidos del proyecto', async () => {
    const api = makeApi([ITEM('m-1')]);
    const { result } = renderHook(() => usePermissionsTab(api, 'proj_1'));
    await waitFor(() => expect(result.current[0]?.badge).toBe(1));
    render(<>{result.current[0].render()}</>);
    expect(await screen.findByTestId('human-inbox-item-m-1')).toBeTruthy();
    expect(screen.getByTestId('permissions-tab')).toBeTruthy();
  });
});
