// @vitest-environment jsdom
// F1.6 (franja de procedencia, bandeja del humano, auditoria y parada global).
import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { resetPaneSessionMemory } from './terminals/terminalModel';
import { PROJECT, makeMatrixApi, renderMatrix, resetTerminalTestState } from './terminals/terminalTestKit';

vi.mock('@xterm/xterm', () => import('./terminals/xtermTestDouble'));
vi.mock('@xterm/addon-fit', () => import('./terminals/xtermTestDouble'));

afterEach(() => {
  cleanup();
  resetTerminalTestState();
  resetPaneSessionMemory();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

async function openCli(cliId, paneId) {
  fireEvent.click(await screen.findByTestId(`cli-open-${cliId}`));
  return screen.findByTestId(`terminal-pane-${paneId}`);
}

const PARKED = {
  msg_id: 'park-1',
  reason: 'CHAIN_DEPTH_EXCEEDED',
  from: { kind: 'pane', agent_name: 'claude-1', pane_id: 'pane_1' },
  to: { kind: 'pane', agent_name: 'codex-2', pane_id: 'pane_2' },
  kind: 'note',
  chain: { hop: 2, max_depth: 1 },
  trust: 'pane_authenticated',
  text: 'tercer mensaje de la cadena'
};

function relayApi(over = {}) {
  return {
    relay_list_human_inbox: vi.fn(async () => ({ success: true, items: [PARKED] })),
    relay_audit_status: vi.fn(async () => ({
      success: true,
      chain: { ok: true, rows: 3 },
      messages: { parked: 1 },
      global_paused: false,
      paused_panes: []
    })),
    relay_resolve_parked: vi.fn(async () => ({ success: true, state: 'queued' })),
    relay_global_pause: vi.fn(async (enabled) => ({ success: true, global_paused: enabled, paused_panes: [] })),
    relay_send_as_human: vi.fn(async () => ({ success: true, state: 'queued', delivered: true })),
    ...over
  };
}

describe('SwarmTerminalMatrix: relevo entre agentes (F1.6)', () => {
  it('cada panel muestra su identidad estable corta en la franja de procedencia', async () => {
    renderMatrix(makeMatrixApi());
    await openCli('claude', 'pane_1');
    const node = await screen.findByTestId('pane-provenance-node-pane_1');
    expect(node.textContent).toBe('pane_1_abcd1');
    expect(node.getAttribute('title')).toContain('pane_1_abcd1234');
  });

  it('el slider muestra bandeja del humano, auditoria, parada global y envio cuando el puente los tiene', async () => {
    const api = makeMatrixApi(relayApi());
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    expect(await screen.findByTestId('human-inbox-item-park-1')).toBeTruthy();
    expect(api.relay_list_human_inbox).toHaveBeenCalledWith(PROJECT.project_uuid);
    expect(screen.getByTestId('human-inbox-text-park-1').textContent).toBe('tercer mensaje de la cadena');
    expect(screen.getByTestId('relay-audit-chain').getAttribute('data-chain-ok')).toBe('true');
    expect(screen.getByTestId('relay-global-pause-switch')).toBeTruthy();
    expect(screen.getByTestId('send-to-agent-target').textContent).toContain('Terminal 1');
  });

  it('sin metodos del relevo en el puente no hay seccion de relevo', async () => {
    renderMatrix(makeMatrixApi());
    await openCli('claude', 'pane_1');
    await screen.findByTestId('active-row-pane_1');
    expect(screen.queryByTestId('relay-panel')).toBeNull();
  });

  it('liberar un mensaje retenido llama a relay_resolve_parked y refresca la bandeja', async () => {
    let items = [PARKED];
    const api = makeMatrixApi(
      relayApi({
        relay_list_human_inbox: vi.fn(async () => ({ success: true, items })),
        relay_resolve_parked: vi.fn(async () => {
          items = [];
          return { success: true, state: 'queued' };
        })
      })
    );
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('human-inbox-release-park-1'));
    await waitFor(() => expect(api.relay_resolve_parked).toHaveBeenCalledWith('park-1', 'release'));
    await waitFor(() => expect(screen.getByTestId('human-inbox-empty')).toBeTruthy());
  });

  it('la franja marca la pausa del propio panel (por node_uuid) y la parada global en todos', async () => {
    let state = { global_paused: false, paused_panes: ['pane_2_abcd1234'] };
    const api = makeMatrixApi(
      relayApi({
        relay_audit_status: vi.fn(async () => ({ success: true, chain: { ok: true, rows: 1 }, messages: {}, ...state })),
        relay_global_pause: vi.fn(async (enabled) => {
          state = { ...state, global_paused: enabled };
          return { success: true, global_paused: enabled, paused_panes: state.paused_panes };
        })
      })
    );
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('codex', 'pane_2');
    await waitFor(() => expect(screen.getByTestId('pane-provenance-paused-pane_2').textContent).toBe('Relevo en pausa'));
    expect(screen.queryByTestId('pane-provenance-paused-pane_1')).toBeNull();

    fireEvent.click(screen.getByTestId('relay-global-pause-switch'));
    await waitFor(() => expect(api.relay_global_pause).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.getByTestId('pane-provenance-paused-pane_1').textContent).toBe('Relevo detenido'));
    expect(screen.getByTestId('pane-provenance-paused-pane_2').textContent).toBe('Relevo detenido');
  });

  it('enviar a un agente desde el slider llama a relay_send_as_human con el proyecto y el panel elegido', async () => {
    const api = makeMatrixApi(relayApi());
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('codex', 'pane_2');
    fireEvent.change(await screen.findByTestId('send-to-agent-target'), { target: { value: 'pane_2' } });
    fireEvent.change(screen.getByTestId('send-to-agent-text'), { target: { value: 'revisa develop' } });
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() =>
      expect(api.relay_send_as_human).toHaveBeenCalledWith(PROJECT.project_uuid, 'pane_2', 'revisa develop', 'note')
    );
    expect(api.pty_write_input).not.toHaveBeenCalled(); // por la bandeja, nunca por el teclado de la terminal
  });

  it('long_descriptions_removed_and_info_hints_present', async () => {
    const onOpenSettings = vi.fn();
    const api = makeMatrixApi({
      pty_set_auto_relay: vi.fn(async () => ({ success: true }))
    });
    renderMatrix(api, { onOpenSettings });
    await openCli('claude', 'pane_1');

    expect(screen.queryByTestId('auto-relay-section')).toBeNull();
    expect(screen.queryByTestId('auto-relay-description')).toBeNull();

    const toggleAuto = await screen.findByTestId('pane-auto-relay-toggle-pane_1');
    expect(toggleAuto.getAttribute('title')).toBeNull();

    const hintAuto = screen.getByTestId('pane-auto-relay-hint-pane_1');
    fireEvent.click(hintAuto);
    expect(onOpenSettings).toHaveBeenNthCalledWith(1, 'terminals_relay', 'ayuda-auto-relay');

    // Contrato 375 px: ningún estilo en línea en ActiveTerminalRow fija un ancho > 343 px.
    const row = screen.getByTestId('active-row-pane_1');
    for (const el of [row, ...row.querySelectorAll('*')]) {
      for (const prop of ['width', 'minWidth']) {
        const val = el.style?.[prop] || '';
        const pxMatch = /^(\d+(?:\.\d+)?)px$/i.exec(val.trim());
        if (pxMatch) {
          expect(Number(pxMatch[1])).toBeLessThanOrEqual(343);
        }
      }
    }
  });
});

describe('SwarmTerminalMatrix: entrega directa (WP-6b)', () => {
  it('el interruptor cicla el modo por terminal y el motivo sale del sondeo de relay_get_delivery_status', async () => {
    const pty_set_delivery_mode = vi.fn(async (paneId, mode) => ({ success: true, pane_id: paneId, delivery_mode: mode }));
    const relay_get_delivery_status = vi.fn(async () => ({
      success: true,
      panes: [{ pane_id: 'pane_1', effective_mode: 'inbox', mode_reason: 'CLI_NOT_VERIFIED', now_reason: null, in_flight: false, queued: 0 }]
    }));
    renderMatrix(makeMatrixApi({ pty_set_delivery_mode, relay_get_delivery_status }));
    await openCli('claude', 'pane_1');

    const toggle = await screen.findByTestId('pane-delivery-toggle-pane_1');
    expect(toggle.textContent).toContain('Entrega directa: AUTO');
    expect(await screen.findByTestId('pane-delivery-reason-pane_1')).toBeTruthy();
    expect(screen.getByTestId('pane-delivery-reason-pane_1').textContent).toContain('solo bandeja');

    fireEvent.click(toggle);
    await waitFor(() => expect(pty_set_delivery_mode).toHaveBeenCalledWith('pane_1', 'direct'));
    await waitFor(() => expect(screen.getByTestId('pane-delivery-toggle-pane_1').textContent).toContain('Entrega directa: ON'));
    expect(relay_get_delivery_status).toHaveBeenCalledWith(PROJECT.project_uuid);
  });

  it('sin el metodo en el puente no hay interruptor de entrega ni sondeo', async () => {
    renderMatrix(makeMatrixApi({}));
    await openCli('claude', 'pane_1');
    expect(screen.queryByTestId('pane-delivery-toggle-pane_1')).toBeNull();
  });
});
