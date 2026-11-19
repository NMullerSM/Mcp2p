// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import React from 'react';
import { RelayPanel } from './RelayPanel';
import { useRelayState } from './useRelayState';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const PARKED = {
  msg_id: 'm-9',
  reason: 'CHAIN_DEPTH_EXCEEDED',
  from: { kind: 'pane', agent_name: 'claude-1', pane_id: 'pane_1' },
  to: { kind: 'pane', agent_name: 'codex-2', pane_id: 'pane_2' },
  kind: 'note',
  chain: { hop: 2, max_depth: 1 },
  trust: 'pane_authenticated',
  text: 'tercer mensaje'
};

function makeApi(over = {}) {
  let parked = [PARKED];
  let paused = false;
  let pausedPanes = [];
  return {
    relay_list_human_inbox: vi.fn(async () => ({ success: true, items: parked })),
    relay_audit_status: vi.fn(async () => ({
      success: true,
      chain: { ok: true, rows: 4 },
      messages: { parked: parked.length },
      global_paused: paused,
      paused_panes: pausedPanes
    })),
    relay_resolve_parked: vi.fn(async (_id, decision) => {
      parked = [];
      return { success: true, state: decision === 'release' ? 'queued' : 'discarded' };
    }),
    relay_global_pause: vi.fn(async (enabled) => {
      paused = enabled;
      return { success: true, global_paused: enabled, paused_panes: pausedPanes };
    }),
    relay_resume_pane: vi.fn(async (nodeUuid) => {
      pausedPanes = pausedPanes.filter((id) => id !== nodeUuid);
      return { success: true, node_uuid: nodeUuid, paused_panes: pausedPanes };
    }),
    relay_send_as_human: vi.fn(async () => ({ success: true, state: 'queued', delivered: true })),
    ...over
  };
}

function Harness({ api, projectUuid = 'proj_1', targets = [{ paneId: 'pane_2', label: 'Terminal 2 · Codex' }], enabled = true }) {
  const relay = useRelayState(api, projectUuid, { enabled, intervalMs: 60000 });
  return <RelayPanel api={api} projectUuid={projectUuid} relay={relay} targets={targets} />;
}

describe('RelayPanel', () => {
  it('carga bandeja y auditoria del proyecto y las muestra', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    expect(await screen.findByTestId('human-inbox-item-m-9')).toBeTruthy();
    expect(api.relay_list_human_inbox).toHaveBeenCalledWith('proj_1');
    expect(screen.getByTestId('relay-audit-chain').getAttribute('data-chain-ok')).toBe('true');
  });

  it('sin metodos del relevo en el puente no pinta nada ni sondea', () => {
    const { container } = render(<Harness api={{}} />);
    expect(container.innerHTML).toBe('');
  });

  it('Liberar llama a relay_resolve_parked, avisa del resultado y recarga la bandeja', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    fireEvent.click(await screen.findByTestId('human-inbox-release-m-9'));
    await waitFor(() => expect(api.relay_resolve_parked).toHaveBeenCalledWith('m-9', 'release'));
    await waitFor(() => expect(screen.getByTestId('human-inbox-feedback').textContent).toContain('ya está en la bandeja'));
    await waitFor(() => expect(screen.getByTestId('human-inbox-empty')).toBeTruthy());
  });

  it('Descartar con el dialogo nativo rechazado deja el mensaje y explica por que', async () => {
    const api = makeApi({
      relay_resolve_parked: vi.fn(async () => ({ success: false, code: 'CONFIRMATION_DECLINED', message: 'La accion fue rechazada en el dialogo de confirmacion.' }))
    });
    render(<Harness api={api} />);
    fireEvent.click(await screen.findByTestId('human-inbox-discard-m-9'));
    await waitFor(() => expect(screen.getByTestId('human-inbox-feedback').textContent).toContain('Rechazó el diálogo'));
    expect(screen.getByTestId('human-inbox-item-m-9')).toBeTruthy();
    expect(screen.getByTestId('human-inbox-release-m-9').disabled).toBe(false);
  });

  it('un error del puente al resolver se muestra con causa y accion', async () => {
    const api = makeApi({ relay_resolve_parked: vi.fn(async () => { throw new Error('puente caido'); }) });
    render(<Harness api={api} />);
    fireEvent.click(await screen.findByTestId('human-inbox-release-m-9'));
    await waitFor(() => expect(screen.getByTestId('human-inbox-feedback').textContent).toContain('No se pudo liberar el mensaje'));
    expect(screen.getByTestId('human-inbox-feedback').textContent).toContain('puente caido');
  });

  it('un fallo al leer la bandeja se muestra con causa y no oculta la auditoria', async () => {
    const api = makeApi({ relay_list_human_inbox: vi.fn(async () => ({ success: false, code: 'RELAY_UNAVAILABLE' })) });
    render(<Harness api={api} />);
    expect(await screen.findByTestId('human-inbox-error')).toBeTruthy();
    expect(screen.getByTestId('human-inbox-error').textContent).toContain('No se pudo leer la bandeja del humano');
    expect(screen.getByTestId('human-inbox-error').textContent).toContain('Reinicie MCP2P');
    expect(screen.getByTestId('relay-audit-chain')).toBeTruthy();
  });

  it('el interruptor de parada global llama a relay_global_pause y refleja el nuevo estado', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    const toggle = await screen.findByTestId('relay-global-pause-switch');
    fireEvent.click(toggle);
    await waitFor(() => expect(api.relay_global_pause).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.getByTestId('relay-global-pause-switch').getAttribute('aria-checked')).toBe('true'));
    expect(screen.getByTestId('relay-status-feedback').textContent).toContain('Relevo detenido');
    fireEvent.click(screen.getByTestId('relay-global-pause-switch'));
    await waitFor(() => expect(api.relay_global_pause).toHaveBeenLastCalledWith(false));
    await waitFor(() => expect(screen.getByTestId('relay-global-pause-switch').getAttribute('aria-checked')).toBe('false'));
  });

  it('reanudar con el dialogo rechazado mantiene la parada y lo dice', async () => {
    const api = makeApi({
      relay_audit_status: vi.fn(async () => ({ success: true, chain: { ok: true, rows: 1 }, messages: {}, global_paused: true, paused_panes: [] })),
      relay_global_pause: vi.fn(async () => ({ success: false, code: 'CONFIRMATION_DECLINED' }))
    });
    render(<Harness api={api} />);
    await waitFor(() => expect(screen.getByTestId('relay-global-pause-switch').getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(screen.getByTestId('relay-global-pause-switch'));
    await waitFor(() => expect(screen.getByTestId('relay-status-feedback').textContent).toContain('Rechazó el diálogo'));
    expect(screen.getByTestId('relay-global-pause-switch').getAttribute('aria-checked')).toBe('true');
  });

  it('enviar a un agente llama a relay_send_as_human(proyecto, panel, texto, tipo) y confirma', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    fireEvent.change(await screen.findByTestId('send-to-agent-text'), { target: { value: 'hola agente' } });
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() => expect(api.relay_send_as_human).toHaveBeenCalledWith('proj_1', 'pane_2', 'hola agente', 'note'));
    await waitFor(() => expect(screen.getByTestId('send-to-agent-result').textContent).toContain('Enviado a la bandeja de Terminal 2'));
  });

  it('un mensaje retenido por la parada global se avisa como guardado, no como entregado', async () => {
    const api = makeApi({ relay_send_as_human: vi.fn(async () => ({ success: true, state: 'queued', delivered: false, held: 'RELAY_PAUSED' })) });
    render(<Harness api={api} />);
    fireEvent.change(await screen.findByTestId('send-to-agent-text'), { target: { value: 'hola' } });
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() => expect(screen.getByTestId('send-to-agent-result').textContent).toContain('el relevo está detenido'));
  });

  it('un rechazo del backend al enviar se explica con causa y accion', async () => {
    const api = makeApi({ relay_send_as_human: vi.fn(async () => ({ success: false, code: 'TARGET_NOT_FOUND', message: 'x' })) });
    render(<Harness api={api} />);
    fireEvent.change(await screen.findByTestId('send-to-agent-text'), { target: { value: 'hola' } });
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() => expect(screen.getByTestId('send-to-agent-result').textContent).toContain('ya no está abierta'));
  });

  it('con la vista oculta (enabled=false) no sondea', async () => {
    const api = makeApi();
    render(<Harness api={api} enabled={false} />);
    await act(async () => {});
    expect(api.relay_list_human_inbox).not.toHaveBeenCalled();
    expect(api.relay_audit_status).not.toHaveBeenCalled();
  });

  it('sondea periodicamente mientras la vista esta a la vista', async () => {
    vi.useFakeTimers();
    const api = makeApi();
    function Fast() {
      const relay = useRelayState(api, 'proj_1', { enabled: true, intervalMs: 1000 });
      return <span data-testid="n">{relay.inbox.items.length}</span>;
    }
    render(<Fast />);
    await act(async () => {});
    const first = api.relay_audit_status.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(api.relay_audit_status.mock.calls.length).toBeGreaterThanOrEqual(first + 2);
  });

  it('Reanudar panel llama a relay_resume_pane, confirma y recarga relay_audit_status', async () => {
    let pausedPanes = ['pane-1234567890ab'];
    const api = makeApi({
      relay_audit_status: vi.fn(async () => ({
        success: true,
        chain: { ok: true, rows: 4 },
        messages: { parked: 1 },
        global_paused: false,
        paused_panes: pausedPanes
      })),
      relay_resume_pane: vi.fn(async (nodeUuid) => {
        pausedPanes = pausedPanes.filter((id) => id !== nodeUuid);
        return { success: true, node_uuid: nodeUuid, paused_panes: pausedPanes };
      })
    });
    render(<Harness api={api} />);
    const btn = await screen.findByTestId('relay-resume-pane-pane-1234567890ab');
    const callsBefore = api.relay_audit_status.mock.calls.length;
    fireEvent.click(btn);
    await waitFor(() => expect(api.relay_resume_pane).toHaveBeenCalledWith('pane-1234567890ab'));
    await waitFor(() => expect(screen.getByTestId('relay-status-feedback').textContent).toContain('Relevo del panel reanudado.'));
    await waitFor(() => expect(api.relay_audit_status.mock.calls.length).toBeGreaterThan(callsBefore));
    await waitFor(() => expect(screen.queryByTestId('relay-paused-panes')).toBeNull());
  });

  it('si relay_resume_pane devuelve CONFIRMATION_DECLINED muestra el mensaje amigable sin romper', async () => {
    const api = makeApi({
      relay_audit_status: vi.fn(async () => ({
        success: true,
        chain: { ok: true, rows: 4 },
        messages: { parked: 1 },
        global_paused: false,
        paused_panes: ['pane-1234567890ab']
      })),
      relay_resume_pane: vi.fn(async () => ({ success: false, code: 'CONFIRMATION_DECLINED' }))
    });
    render(<Harness api={api} />);
    const btn = await screen.findByTestId('relay-resume-pane-pane-1234567890ab');
    fireEvent.click(btn);
    await waitFor(() => expect(api.relay_resume_pane).toHaveBeenCalledWith('pane-1234567890ab'));
    await waitFor(() => expect(screen.getByTestId('relay-status-feedback').textContent).toContain('Rechazó el diálogo'));
    expect(screen.getByTestId('relay-resume-pane-pane-1234567890ab').disabled).toBe(false);
    expect(screen.getByTestId('relay-resume-pane-pane-1234567890ab').textContent).toBe('Reanudar');
    expect(screen.getByTestId('relay-global-pause-switch').disabled).toBe(false);
  });

  it('si relay_resume_pane lanza una excepcion muestra el error y limpia el estado bloqueado', async () => {
    const api = makeApi({
      relay_audit_status: vi.fn(async () => ({
        success: true,
        chain: { ok: true, rows: 4 },
        messages: { parked: 1 },
        global_paused: false,
        paused_panes: ['pane-1234567890ab']
      })),
      relay_resume_pane: vi.fn(async () => {
        throw new Error('puente caido');
      })
    });
    render(<Harness api={api} />);
    const btn = await screen.findByTestId('relay-resume-pane-pane-1234567890ab');
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId('relay-status-feedback').textContent).toContain('No se pudo reanudar el relevo del panel'));
    expect(screen.getByTestId('relay-status-feedback').textContent).toContain('puente caido');
    expect(screen.getByTestId('relay-resume-pane-pane-1234567890ab').disabled).toBe(false);
    expect(screen.getByTestId('relay-global-pause-switch').disabled).toBe(false);
  });

  it('Descartar pasando por CONFIRMATION_REQUIRED abre AppConfirmDialog y confirma con ui_confirm_action', async () => {
    let parked = [PARKED];
    const api = makeApi({
      relay_list_human_inbox: vi.fn(async () => ({ success: true, items: parked })),
      relay_resolve_parked: vi.fn(async (_id, decision) => {
        if (decision === 'discard') {
          return {
            success: false,
            code: 'CONFIRMATION_REQUIRED',
            confirmation: {
              token: 'tok-discard-9',
              action_id: 'relay.discard_parked',
              title: 'Descartar un mensaje entre agentes',
              message: 'Texto exacto:\ntercer mensaje',
              rememberable: false,
              expires_in_s: 120
            }
          };
        }
        parked = [];
        return { success: true, state: 'queued' };
      }),
      ui_confirm_action: vi.fn(async () => {
        parked = [];
        return { success: true, state: 'discarded' };
      })
    });

    render(<Harness api={api} />);
    fireEvent.click(await screen.findByTestId('human-inbox-discard-m-9'));
    expect(await screen.findByRole('alertdialog')).toBeTruthy();
    expect(screen.queryByTestId('app-confirm-remember')).toBeNull();

    await waitFor(() => expect(screen.getByTestId('app-confirm-accept').disabled).toBe(false), { timeout: 1500 });
    fireEvent.click(screen.getByTestId('app-confirm-accept'));

    await waitFor(() => expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-discard-9', true, false));
    await waitFor(() => expect(screen.getByTestId('human-inbox-feedback').textContent).toContain('Mensaje descartado'));
    await waitFor(() => expect(screen.getByTestId('human-inbox-empty')).toBeTruthy());
  });
});
