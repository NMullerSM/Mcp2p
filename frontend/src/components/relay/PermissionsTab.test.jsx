// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import React from 'react';
import { PermissionsTab } from './PermissionsTab';
import { useRelayState } from './useRelayState';

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const LOCAL = {
  msg_id: 'm-local',
  reason: 'CHAIN_DEPTH_EXCEEDED',
  from: { kind: 'pane', agent_name: 'claude-1', pane_id: 'pane_1' },
  to: { kind: 'pane', agent_name: 'codex-2', pane_id: 'pane_2' },
  kind: 'note',
  chain: { hop: 2, max_depth: 1 },
  trust: 'pane_authenticated',
  text: 'mensaje local'
};

const NODE = {
  msg_id: 'm-node',
  reason: 'UNTRUSTED_NODE',
  from: { kind: 'pane', node_uuid: 'remote-pane-1', pane_id: null, agent_name: null },
  to: { kind: 'pane', node_uuid: 'local-pane-2', pane_id: 'pane_2', agent_name: 'codex-2' },
  kind: 'note',
  chain: { chain_id: 'c-1', hop: 0, max_depth: 1 },
  trust: 'unverified',
  text: 'Hola desde el portátil',
  created_at: 1760000000000,
  expires_at: 1760086400000,
  remote: {
    display_name: 'Portátil',
    fingerprint_short: 'A1B2-…-0708',
    device_fingerprint: 'a1b2c3d4e5f60708',
    relation: 'foreign',
    transport: 'simulated',
    name_collision: false,
    previous_name: null
  }
};

// Puente simulado con estado: liberar o descartar saca el mensaje de la bandeja, como hace el backend.
function makeApi(over = {}) {
  let inbox = [LOCAL, NODE];
  return {
    relay_list_human_inbox: vi.fn(async () => ({ success: true, items: inbox })),
    relay_audit_status: vi.fn(async () => ({ success: true, chain: { ok: true, rows: 1 }, messages: {}, global_paused: false, paused_panes: [] })),
    relay_resolve_parked: vi.fn(async (msgId) => {
      inbox = inbox.filter((item) => item.msg_id !== msgId);
      return { success: true, state: 'queued' };
    }),
    relay_set_node_policy: vi.fn(async () => ({ success: true })),
    ui_confirm_action: vi.fn(async () => ({ success: true, code: 'OK' })),
    __setInbox: (items) => {
      inbox = items;
    },
    ...over
  };
}

function Harness({ api, projectUuid = 'proj_1' }) {
  const relay = useRelayState(api, projectUuid, { enabled: true, intervalMs: 60000 });
  return <PermissionsTab api={api} relay={relay} />;
}

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe('PermissionsTab', () => {
  it('sin nada pendiente muestra el estado vacio', async () => {
    const api = makeApi({ relay_list_human_inbox: vi.fn(async () => ({ success: true, items: [] })) });
    render(<Harness api={api} />);
    expect(await screen.findByTestId('permissions-empty')).toBeTruthy();
    expect(screen.getByTestId('permissions-empty').textContent).toBe('No hay permisos pendientes.');
    expect(screen.queryByTestId('permissions-local')).toBeNull();
    expect(screen.queryByTestId('permissions-nodes')).toBeNull();
  });

  it('separa mensajes retenidos y mensajes de otros nodos en dos secciones con titulo', async () => {
    render(<Harness api={makeApi()} />);
    const local = await screen.findByTestId('permissions-local');
    const nodes = await screen.findByTestId('permissions-nodes');
    expect(local.querySelector('h3').textContent).toBe('Mensajes retenidos');
    expect(nodes.querySelector('h3').textContent).toBe('Mensajes de otros nodos');
    expect(screen.getByTestId('human-inbox-item-m-local')).toBeTruthy();
    expect(screen.getByTestId('node-request-card-m-node')).toBeTruthy();
    expect(screen.queryByTestId('human-inbox-item-m-node')).toBeNull();
  });

  it('Liberar un retenido local lo entrega, muestra el resultado y recarga la lista', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    fireEvent.click(await screen.findByTestId('human-inbox-release-m-local'));
    await waitFor(() => expect(api.relay_resolve_parked).toHaveBeenCalledWith('m-local', 'release'));
    expect(await screen.findByTestId('permissions-feedback')).toBeTruthy();
    expect(screen.getByTestId('permissions-feedback').textContent).toContain('ya está en la bandeja');
    await waitFor(() => expect(screen.queryByTestId('human-inbox-item-m-local')).toBeNull());
  });

  it('Descartar un retenido local pide la confirmacion propia de la app y solo entonces llama al puente', async () => {
    const api = makeApi({
      relay_resolve_parked: vi.fn(async () => ({ success: false, code: 'CONFIRMATION_REQUIRED', confirmation: { token: 'tok-d', message: 'Texto exacto del descarte' } }))
    });
    render(<Harness api={api} />);
    fireEvent.click(await screen.findByTestId('human-inbox-discard-m-local'));
    expect(await screen.findByRole('alertdialog')).toBeTruthy();
    expect(screen.getByTestId('app-confirm-message').textContent).toBe('Texto exacto del descarte');
    expect(api.relay_resolve_parked).toHaveBeenCalledWith('m-local', 'discard');
    // El botón Confirmar se arma tras 600 ms (anti-clic accidental): esperamos a que esté activo.
    await waitFor(() => expect(screen.getByTestId('app-confirm-accept').disabled).toBe(false));
    fireEvent.click(screen.getByTestId('app-confirm-accept'));
    await waitFor(() => expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-d', true, false));
    await waitFor(() => expect(screen.getByTestId('permissions-feedback').textContent).toContain('Mensaje descartado'));
  });

  it('Entregar un mensaje de nodo tras los 1,5 s llama a relay_resolve_parked con release', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    await screen.findByTestId('node-request-card-m-node');
    const approve = screen.getByTestId('node-request-approve-m-node');
    expect(approve.disabled).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(approve.disabled).toBe(false);
    fireEvent.click(approve);
    await waitFor(() => expect(api.relay_resolve_parked).toHaveBeenCalledWith('m-node', 'release'));
  });

  it('Bloquear nodo llama a relay_set_node_policy con la huella y recarga; el error del puente se explica', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    await screen.findByTestId('node-request-card-m-node');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    fireEvent.click(screen.getByTestId('node-request-block-m-node'));
    await waitFor(() => expect(api.relay_set_node_policy).toHaveBeenCalledWith('a1b2c3d4e5f60708', 'blocked'));
    await waitFor(() => expect(screen.getByTestId('permissions-policy-feedback').textContent).toContain('Política del nodo actualizada'));
    expect(api.relay_audit_status.mock.calls.length).toBeGreaterThan(1);
  });

  it('un fallo al bloquear el nodo se muestra con causa y no rompe la lista', async () => {
    const api = makeApi({ relay_set_node_policy: vi.fn(async () => { throw new Error('puente caido'); }) });
    render(<Harness api={api} />);
    await screen.findByTestId('node-request-card-m-node');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    fireEvent.click(screen.getByTestId('node-request-block-m-node'));
    await waitFor(() => expect(screen.getByTestId('permissions-policy-feedback').getAttribute('role')).toBe('alert'));
    expect(screen.getByTestId('permissions-policy-feedback').textContent).toContain('No se pudo bloquear el nodo');
    expect(screen.getByTestId('permissions-policy-feedback').textContent).toContain('puente caido');
    expect(screen.getByTestId('node-request-card-m-node')).toBeTruthy();
  });

  it('el texto HTML de un mensaje se pinta como texto, nunca como marcado', async () => {
    const hostile = { ...LOCAL, msg_id: 'm-html', text: '<img src="x" onerror="window.__pwned=1"><b>hola</b>' };
    const hostileNode = { ...NODE, msg_id: 'm-html-node', text: '<script>window.__pwned=2</script>' };
    const api = makeApi();
    api.__setInbox([hostile, hostileNode]);
    render(<Harness api={api} />);
    const pre = await screen.findByTestId('human-inbox-text-m-html');
    expect(pre.textContent).toBe('<img src="x" onerror="window.__pwned=1"><b>hola</b>');
    expect(pre.querySelector('img')).toBeNull();
    expect(pre.querySelector('b')).toBeNull();
    expect(screen.getByTestId('node-request-text-m-html-node').textContent).toBe('<script>window.__pwned=2</script>');
    expect(document.querySelector('script[src], [onerror]')).toBeNull();
    expect(window.__pwned).toBeUndefined();
  });

  it('un error del puente al leer la bandeja se muestra con causa y accion', async () => {
    const api = makeApi({ relay_list_human_inbox: vi.fn(async () => ({ success: false, code: 'RELAY_UNAVAILABLE' })) });
    render(<Harness api={api} />);
    const alert = await screen.findByTestId('permissions-error');
    expect(alert.getAttribute('role')).toBe('alert');
    expect(alert.textContent).toContain('No se pudo leer la bandeja del humano');
    expect(alert.textContent).toContain('Reinicie MCP2P');
    expect(screen.queryByTestId('permissions-empty')).toBeNull();
  });

  it('el boton Actualizar vuelve a leer el relevo', async () => {
    const api = makeApi();
    render(<Harness api={api} />);
    await screen.findByTestId('node-request-card-m-node');
    const before = api.relay_list_human_inbox.mock.calls.length;
    fireEvent.click(screen.getByTestId('permissions-refresh'));
    await settle();
    expect(api.relay_list_human_inbox.mock.calls.length).toBeGreaterThan(before);
    expect(screen.getByTestId('permissions-refresh').textContent).toContain('Actualizar');
  });
});
