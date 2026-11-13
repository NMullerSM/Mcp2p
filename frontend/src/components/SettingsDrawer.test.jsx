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
    await screen.findByText('Servidor de señalización P2P guardado.');
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
