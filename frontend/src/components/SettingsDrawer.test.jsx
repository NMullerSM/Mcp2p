// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { SettingsDrawer } from './SettingsDrawer';

const PROJECT = { project_uuid: 'proj_abc123', name: 'Proyecto Demo' };

function makeApi({ signalingUrl = '' } = {}) {
  return {
    get_system_info: vi.fn(() => Promise.resolve({ success: true, active_node: 'node_owner' })),
    get_github_status: vi.fn(() => Promise.resolve({ success: true, github: { authenticated: false } })),
    get_signaling_server_url: vi.fn(() => Promise.resolve(signalingUrl)),
    set_signaling_server_url: vi.fn((url) => Promise.resolve(
      url === '' || url.startsWith('ws://') || url.startsWith('wss://')
        ? { success: true }
        : { success: false, error: 'La URL debe estar vacía o comenzar con ws:// o wss://' }
    )),
    generate_p2p_invite: vi.fn(() => Promise.resolve({ success: true, invite_url: 'mcp2p://join?token=xyz' })),
    get_mcp_config_snippet: vi.fn(() => Promise.resolve({ success: true, mcp_json: '{}', agent_seed_prompt: 'hola' }))
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SettingsDrawer - Servidor de Señalización P2P', () => {
  it('precarga el campo con la URL devuelta por get_signaling_server_url al abrir', async () => {
    const api = makeApi({ signalingUrl: 'wss://relay.example.com/signal' });
    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="p2p" />
    );

    await waitFor(() => expect(api.get_signaling_server_url).toHaveBeenCalled());
    const input = await screen.findByPlaceholderText('wss://signal.example.com');
    await waitFor(() => expect(input.value).toBe('wss://relay.example.com/signal'));
  });

  it('precarga el campo vacío cuando no hay URL configurada', async () => {
    const api = makeApi({ signalingUrl: '' });
    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="p2p" />
    );

    await waitFor(() => expect(api.get_signaling_server_url).toHaveBeenCalled());
    const input = await screen.findByPlaceholderText('wss://signal.example.com');
    expect(input.value).toBe('');
  });

  it('guarda la URL llamando a set_signaling_server_url y muestra feedback de éxito', async () => {
    const api = makeApi({ signalingUrl: '' });
    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="p2p" />
    );

    const input = await screen.findByPlaceholderText('wss://signal.example.com');
    fireEvent.change(input, { target: { value: 'wss://relay.example.com/signal' } });
    fireEvent.click(screen.getByText('Guardar'));

    await waitFor(() => expect(api.set_signaling_server_url).toHaveBeenCalledWith('wss://relay.example.com/signal'));
    // Hallazgo I1: la URL solo se lee al arrancar (main.py), así que el feedback de
    // éxito debe decir que hace falta reiniciar.
    await screen.findByText('Servidor de señalización P2P guardado. Reinicia la aplicación para aplicarlo.');
  });

  it('marca el campo como experimental e indica que requiere reiniciar (hallazgo I1)', async () => {
    const api = makeApi({ signalingUrl: '' });
    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="p2p" />
    );

    await screen.findByPlaceholderText('wss://signal.example.com');
    expect(screen.getByText('EXPERIMENTAL')).toBeTruthy();
    const note = screen.getByTestId('signaling-experimental-note');
    expect(note.textContent).toContain('todavía no está completa');
    expect(note.textContent).toContain('reiníciala');
  });

  it('muestra un mensaje de error cuando set_signaling_server_url rechaza un esquema inválido', async () => {
    const api = makeApi({ signalingUrl: '' });
    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="p2p" />
    );

    const input = await screen.findByPlaceholderText('wss://signal.example.com');
    fireEvent.change(input, { target: { value: 'https://not-a-ws-url.example.com' } });
    fireEvent.click(screen.getByText('Guardar'));

    await waitFor(() => expect(api.set_signaling_server_url).toHaveBeenCalledWith('https://not-a-ws-url.example.com'));
    await screen.findByText('La URL debe estar vacía o comenzar con ws:// o wss://');
  });

  it('deshabilita el botón Guardar mientras la llamada está en curso', async () => {
    let resolvePromise;
    const api = makeApi({ signalingUrl: '' });
    api.set_signaling_server_url = vi.fn(() => new Promise((resolve) => { resolvePromise = resolve; }));

    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="p2p" />
    );

    await screen.findByPlaceholderText('wss://signal.example.com');
    const saveButton = screen.getByText('Guardar').closest('button');
    fireEvent.click(saveButton);
    expect(saveButton.disabled).toBe(true);

    resolvePromise({ success: true });
    await waitFor(() => expect(screen.getByText('Guardar').closest('button').disabled).toBe(false));
  });
});

describe('SettingsDrawer - Multi-Seed P2P y Laboratorio OKF & Puertos (Hito 4.4)', () => {
  function makeFullLabApi() {
    let seeds = ['wss://seed-1.example.com:8765'];
    let folders = [
      { path: 'docs/okf/adr', category: 'adr', description: 'Decisiones', file_count: 2, required: true },
      { path: 'docs/okf/custom', category: 'custom', description: 'Notas', file_count: 0, required: false }
    ];
    let gates = [
      { connector_id: 'outbound_rest', unlocked: false, allowed_domains: [], allow_private_ips: false, circuit_state: 'closed' },
      { connector_id: 'agent_terminal_matrix', unlocked: false, allowed_domains: [], allow_private_ips: false, circuit_state: 'closed' }
    ];

    return {
      ...makeApi(),
      list_p2p_seeds: vi.fn(async () => ({
        success: true,
        seeds: [...seeds],
        peers: [{ node_uuid: 'peer_node_1', status: 'connected' }]
      })),
      add_p2p_seed: vi.fn(async (_uuid, url) => {
        seeds = [...seeds, url];
        return { success: true, seeds: [...seeds] };
      }),
      remove_p2p_seed: vi.fn(async (_uuid, url) => {
        seeds = seeds.filter(s => s !== url);
        return { success: true, seeds: [...seeds] };
      }),
      list_okf_folders: vi.fn(async () => ({ success: true, folders: [...folders] })),
      add_okf_folder: vi.fn(async (_uuid, folderPath, category, description) => {
        folders = [...folders, { path: folderPath, category, description, file_count: 0, required: false }];
        return { success: true, folders: [...folders] };
      }),
      rename_okf_folder: vi.fn(async (_uuid, oldPath, newPath) => {
        folders = folders.map(f => (f.path === oldPath ? { ...f, path: newPath } : f));
        return { success: true, folders: [...folders] };
      }),
      remove_okf_folder: vi.fn(async (_uuid, folderPath) => {
        folders = folders.filter(f => f.path !== folderPath);
        return { success: true, folders: [...folders] };
      }),
      get_okf_tags_index: vi.fn(async () => ({
        success: true,
        tags: { p2p: ['docs/okf/adr/ADR-001.md'] },
        items: [{ path: 'docs/okf/adr/ADR-001.md', tags: ['p2p'] }]
      })),
      update_artifact_tags: vi.fn(async (_uuid, relPath, tags) => ({
        success: true,
        path: relPath,
        tags,
        index: { items: [{ path: relPath, tags }] }
      })),
      get_okf_freshness_report: vi.fn(async () => ({
        success: true,
        summary: { total: 1, fresh: 1, aging: 0, stale: 0, avg_score: 0.92, verified_signatures: 1, unverified_adrs: 0 },
        items: [{ path: 'docs/okf/adr/ADR-001.md', id: 'ADR-001', title: 'ADR 1', score: 0.92, status: 'FRESH', signature_valid: true, commits_after: 0, days_old: 1, tags: ['p2p'] }]
      })),
      get_extension_gates_status: vi.fn(async () => ({ success: true, gates: [...gates] })),
      unlock_extension_gate: vi.fn(async (connectorId, allowedDomains, allowPrivateIps) => {
        const updated = { connector_id: connectorId, unlocked: true, allowed_domains: allowedDomains, allow_private_ips: allowPrivateIps, circuit_state: 'open' };
        gates = gates.map(g => (g.connector_id === connectorId ? updated : g));
        return { success: true, gate: updated };
      }),
      lock_extension_gate: vi.fn(async (connectorId) => {
        const updated = { connector_id: connectorId, unlocked: false, allowed_domains: [], allow_private_ips: false, circuit_state: 'closed' };
        gates = gates.map(g => (g.connector_id === connectorId ? updated : g));
        return { success: true, gate: updated };
      }),
      test_outbound_rest_gate: vi.fn(async (_connectorId, url) => ({
        success: true,
        blocked_reason: null,
        status_code: 200,
        preview: `Zen OK from ${url}`
      })),
      run_system_diagnostics: vi.fn(async () => ({
        success: true,
        summary: { passed: 2, failed: 0 },
        checks: [
          { name: 'SQLite WAL', status: 'passed', detail: 'Modo WAL activo' },
          { name: 'Firmas Ed25519', status: 'passed', detail: 'Llave válida' }
        ]
      }))
    };
  }

  it('permite listar, agregar y eliminar múltiples Seeds P2P y ver pares conectados', async () => {
    const api = makeFullLabApi();
    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="p2p" />
    );

    await screen.findByText('wss://seed-1.example.com:8765');
    expect(screen.getByText('peer_node_1')).toBeTruthy();

    fireEvent.change(screen.getByTestId('p2p-seed-input'), { target: { value: 'wss://seed-2.example.com:9000' } });
    fireEvent.click(screen.getByTestId('add-p2p-seed-btn'));

    await waitFor(() => expect(api.add_p2p_seed).toHaveBeenCalledWith('proj_abc123', 'wss://seed-2.example.com:9000'));
    await screen.findByText('wss://seed-2.example.com:9000');

    fireEvent.click(screen.getByLabelText('Eliminar seed wss://seed-2.example.com:9000'));
    await waitFor(() => expect(api.remove_p2p_seed).toHaveBeenCalledWith('proj_abc123', 'wss://seed-2.example.com:9000'));
  });

  it('en Laboratorio OKF gestiona carpetas, actualiza tags YAML, ejecuta Freshness, controla Puertas con Llave y corre diagnóstico', async () => {
    const api = makeFullLabApi();
    render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={PROJECT} projectsList={[PROJECT]} initialTab="okf_lab" />
    );

    await screen.findByTestId('okf-lab-panel');
    await screen.findByTestId('okf-folder-row-docs/okf/custom');

    // (a) Agregar, renombrar y eliminar carpeta OKF
    fireEvent.change(screen.getByTestId('okf-new-folder-path'), { target: { value: 'docs/okf/runbooks' } });
    fireEvent.change(screen.getByTestId('okf-new-folder-desc'), { target: { value: 'Runbooks operativos' } });
    fireEvent.click(screen.getByTestId('okf-add-folder-btn'));
    await waitFor(() => expect(api.add_okf_folder).toHaveBeenCalledWith('proj_abc123', 'docs/okf/runbooks', 'custom', 'Runbooks operativos'));
    await screen.findByTestId('okf-folder-row-docs/okf/runbooks');

    fireEvent.click(screen.getByLabelText('Renombrar docs/okf/runbooks'));
    fireEvent.change(screen.getByTestId('okf-rename-input-docs/okf/runbooks'), { target: { value: 'docs/okf/playbooks' } });
    fireEvent.click(screen.getByTestId('okf-rename-confirm-docs/okf/runbooks'));
    await waitFor(() => expect(api.rename_okf_folder).toHaveBeenCalledWith('proj_abc123', 'docs/okf/runbooks', 'docs/okf/playbooks'));
    await screen.findByTestId('okf-folder-row-docs/okf/playbooks');

    fireEvent.click(screen.getByLabelText('Eliminar docs/okf/playbooks'));
    await waitFor(() => expect(api.remove_okf_folder).toHaveBeenCalledWith('proj_abc123', 'docs/okf/playbooks', true));

    // (b) Editor de Tags YAML + Freshness
    fireEvent.change(screen.getByTestId('okf-tag-values-input'), { target: { value: 'p2p, hexagonal' } });
    fireEvent.click(screen.getByTestId('okf-save-tags-btn'));
    await waitFor(() => expect(api.update_artifact_tags).toHaveBeenCalledWith('proj_abc123', 'docs/okf/adr/ADR-001.md', ['p2p', 'hexagonal']));

    fireEvent.click(screen.getByTestId('run-freshness-btn'));
    await waitFor(() => expect(api.get_okf_freshness_report).toHaveBeenCalledTimes(2));

    // (c) Puerta con Llave: desbloquear, probar URL REST y cerrar
    fireEvent.change(screen.getByTestId('gate-domains-outbound_rest'), { target: { value: 'api.github.com' } });
    fireEvent.click(screen.getByTestId('unlock-gate-outbound_rest'));
    await waitFor(() => expect(api.unlock_extension_gate).toHaveBeenCalledWith('outbound_rest', ['api.github.com'], false));
    expect(screen.getByTestId('gate-card-outbound_rest').textContent).toContain('DESBLOQUEADA');

    fireEvent.change(screen.getByTestId('rest-gate-test-url'), { target: { value: 'https://api.github.com/zen' } });
    fireEvent.click(screen.getByTestId('rest-gate-test-btn'));
    await waitFor(() => expect(api.test_outbound_rest_gate).toHaveBeenCalledWith('outbound_rest', 'https://api.github.com/zen'));
    expect((await screen.findByTestId('rest-gate-test-result')).textContent).toContain('Zen OK');

    fireEvent.click(screen.getByTestId('lock-gate-outbound_rest'));
    await waitFor(() => expect(api.lock_extension_gate).toHaveBeenCalledWith('outbound_rest'));

    // (d) Diagnóstico en vivo
    fireEvent.click(screen.getByTestId('run-diagnostics-btn'));
    await waitFor(() => expect(api.run_system_diagnostics).toHaveBeenCalledWith('proj_abc123'));
    await screen.findByText('SQLite WAL');
    expect(screen.getByText('2 OK')).toBeTruthy();
  });
});
