// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { SwarmTerminalMatrix } from './SwarmTerminalMatrix';
import { AppHeader } from './AppHeader';
import { ProjectWorkspace } from './ProjectWorkspace';

const PROJECT = { project_uuid: 'proj_matrix_1', name: 'Proyecto Matrix' };

const MANUAL_CMD =
  'mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765';
const MCP_CONFIG = {
  mcpServers: {
    mcp2p: {
      url: 'http://127.0.0.1:8765/mcp'
    }
  }
};

function makeMatrixApi(overrides = {}) {
  return {
    pty_get_matrix_status: vi.fn(async () => ({
      success: true,
      degraded: false,
      connector_status: { available: true, degraded: false, mode: 'posix_pty' },
      hub_url: null,
      hub_running: false,
      panes: [],
      sessions: [],
      manual_command: null,
      mcp_config: null
    })),
    pty_spawn_pane: vi.fn(async (paneId, cliProfile, role, _cmd, cols, rows) => ({
      success: true,
      degraded: false,
      pane_id: paneId,
      node_uuid: `${paneId}_abcd1234`,
      pid: 4242,
      hub_url: 'http://127.0.0.1:51234/mcp',
      pane: {
        pane_id: paneId,
        node_uuid: `${paneId}_abcd1234`,
        cli_profile: cliProfile,
        role,
        pid: 4242,
        status: 'active'
      },
      session: {
        pane_id: paneId,
        session_id: `${paneId}-sess1`,
        cli_profile: cliProfile,
        role,
        node_uuid: `${paneId}_abcd1234`,
        pid: 4242,
        alive: true,
        paused: false,
        rss_mb: 24.5,
        cols,
        rows
      }
    })),
    pty_poll_output: vi.fn(async (paneId, sinceSeq = 0) => ({
      success: true,
      pane_id: paneId,
      alive: true,
      paused: false,
      rss_mb: 24.5,
      since_seq: sinceSeq,
      next_seq: sinceSeq === 0 ? 1 : sinceSeq,
      truncated: false,
      chunks: sinceSeq === 0 ? [{ seq: 1, text: 'Agente listo en https://docs.example.com/auth\n' }] : [],
      text: sinceSeq === 0 ? 'Agente listo en https://docs.example.com/auth\n' : ''
    })),
    pty_write_input: vi.fn(async (paneId, data) => ({
      success: true,
      pane_id: paneId,
      bytes_written: data.length
    })),
    pty_resize_pane: vi.fn(async (paneId, cols, rows) => ({
      success: true,
      pane_id: paneId,
      cols,
      rows
    })),
    pty_pause_pane: vi.fn(async (paneId, paused) => ({
      success: true,
      pane_id: paneId,
      paused
    })),
    pty_close_pane: vi.fn(async (paneId) => ({
      success: true,
      pane_id: paneId,
      closed: true
    })),
    ...overrides
  };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('SwarmTerminalMatrix', () => {
  it('renderiza la matriz de 4 panes por defecto y permite cambiar entre layouts 1x1, 2x1 y 2x2', async () => {
    const api = makeMatrixApi();
    render(<SwarmTerminalMatrix api={api} project={PROJECT} />);

    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalledTimes(1));

    expect(screen.getByTestId('terminal-pane-pane_1')).toBeTruthy();
    expect(screen.getByTestId('terminal-pane-pane_2')).toBeTruthy();
    expect(screen.getByTestId('terminal-pane-pane_3')).toBeTruthy();
    expect(screen.getByTestId('terminal-pane-pane_4')).toBeTruthy();

    fireEvent.click(screen.getByTestId('layout-btn-2x1'));
    expect(screen.getByTestId('terminal-pane-pane_1')).toBeTruthy();
    expect(screen.getByTestId('terminal-pane-pane_2')).toBeTruthy();
    expect(screen.queryByTestId('terminal-pane-pane_3')).toBeNull();
    expect(screen.queryByTestId('terminal-pane-pane_4')).toBeNull();

    fireEvent.click(screen.getByTestId('layout-btn-1x1'));
    expect(screen.getByTestId('terminal-pane-pane_1')).toBeTruthy();
    expect(screen.queryByTestId('terminal-pane-pane_2')).toBeNull();

    fireEvent.click(screen.getByTestId('layout-btn-2x2'));
    expect(screen.getByTestId('terminal-pane-pane_4')).toBeTruthy();
  });

  it('inicia un pane con pty_spawn_pane, hace polling incremental con pty_poll_output y muestra metadatos', async () => {
    const api = makeMatrixApi();
    render(<SwarmTerminalMatrix api={api} project={PROJECT} />);

    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId('cli-profile-pane_1'), { target: { value: 'gemini' } });
    fireEvent.change(screen.getByTestId('role-select-pane_1'), { target: { value: 'reviewer' } });
    fireEvent.click(screen.getByTestId('pane-start-btn-pane_1'));

    await waitFor(() =>
      expect(api.pty_spawn_pane).toHaveBeenCalledWith(
        'pane_1',
        'gemini',
        'reviewer',
        null,
        100,
        28,
        true
      )
    );

    await waitFor(() => expect(api.pty_poll_output).toHaveBeenCalledWith('pane_1', 0));

    expect(await screen.findByText(/Agente listo en/)).toBeTruthy();
    expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Activo');
    expect(screen.getByTestId('pane-pid-pane_1').textContent).toContain('4242');
    expect(screen.getByTestId('pane-rss-pane_1').textContent).toContain('24.5 MB');
    expect(screen.getByTestId('pane-node-pane_1').textContent).toContain('pane_1_abcd1');
    expect(screen.getByTestId('hub-status-badge').textContent).toContain('Hub Activo: http://127.0.0.1:51234/mcp');
  });

  it('envía entrada humana vía pty_write_input(pane_id, data, true), soporta Ctrl+C, pausa, resize y cierre', async () => {
    const api = makeMatrixApi();
    render(<SwarmTerminalMatrix api={api} project={PROJECT} />);

    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('pane-start-btn-pane_1'));
    await waitFor(() => expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Activo'));

    const input = screen.getByTestId('pane-input-pane_1');
    fireEvent.change(input, { target: { value: 'status --json' } });
    fireEvent.click(screen.getByTestId('pane-send-pane_1'));

    await waitFor(() =>
      expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', 'status --json\n', true)
    );

    // Ctrl+C rápido
    fireEvent.click(screen.getByTestId('pane-ctrlc-pane_1'));
    await waitFor(() =>
      expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\x03', true)
    );

    // Entrada directa por teclado en el viewport del terminal
    const terminalPre = screen.getByTestId('pane-output-pane_1');
    fireEvent.keyDown(terminalPre, { key: 'x' });
    await waitFor(() =>
      expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', 'x', true)
    );

    // Pausar y reanudar
    fireEvent.click(screen.getByTestId('pane-pause-btn-pane_1'));
    await waitFor(() => expect(api.pty_pause_pane).toHaveBeenCalledWith('pane_1', true));
    expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Pausado');

    fireEvent.click(screen.getByTestId('pane-pause-btn-pane_1'));
    await waitFor(() => expect(api.pty_pause_pane).toHaveBeenCalledWith('pane_1', false));

    // Redimensionar
    fireEvent.change(screen.getByTestId('size-select-pane_1'), { target: { value: '120x36' } });
    await waitFor(() => expect(api.pty_resize_pane).toHaveBeenCalledWith('pane_1', 120, 36));

    // Cerrar
    fireEvent.click(screen.getByTestId('pane-close-btn-pane_1'));
    await waitFor(() => expect(api.pty_close_pane).toHaveBeenCalledWith('pane_1'));
    expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Libre');
  });

  it('protege las URLs detectadas en salida TTY con diálogo de confirmación explícito antes de abrirlas', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const api = makeMatrixApi();
    render(<SwarmTerminalMatrix api={api} project={PROJECT} />);

    fireEvent.click(screen.getByTestId('pane-start-btn-pane_1'));
    const urlBtn = await screen.findByTestId('tty-detected-url');
    expect(urlBtn.textContent).toBe('https://docs.example.com/auth');

    fireEvent.click(urlBtn);
    expect(openSpy).not.toHaveBeenCalled();

    const modal = await screen.findByTestId('external-url-confirm-modal');
    expect(modal.textContent).toContain('https://docs.example.com/auth');

    fireEvent.click(screen.getByTestId('external-url-confirm-btn'));
    expect(openSpy).toHaveBeenCalledWith(
      'https://docs.example.com/auth',
      '_blank',
      'noopener,noreferrer'
    );
  });

  it('muestra la tarjeta de modo degradado RF-STM-12 con manual_command y mcpServers cuando pty_get_matrix_status devuelve degraded: true', async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true
    });

    const api = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true,
        degraded: true,
        connector_status: { available: false, degraded: true, mode: 'null_degraded' },
        hub_url: null,
        hub_running: false,
        panes: [],
        sessions: [],
        manual_command: MANUAL_CMD,
        mcp_config: MCP_CONFIG
      }))
    });

    render(<SwarmTerminalMatrix api={api} project={PROJECT} />);

    const banner = await screen.findByTestId('matrix-degraded-banner');
    expect(banner.textContent).toContain('RF-STM-12');
    expect(banner.textContent).toContain(MANUAL_CMD);
    expect(banner.textContent).toContain('mcpServers');
    expect(screen.getByTestId('hub-status-badge').textContent).toContain('Degradado');

    fireEvent.click(screen.getByTestId('matrix-degraded-banner-copy-cmd'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(MANUAL_CMD));
  });

  it('muestra tarjeta RF-STM-12 dentro del propio pane cuando pty_spawn_pane devuelve degraded: true (ej. CLI_BINARY_NOT_FOUND)', async () => {
    const api = makeMatrixApi({
      pty_spawn_pane: vi.fn(async (paneId) => ({
        success: false,
        degraded: true,
        code: 'CLI_BINARY_NOT_FOUND',
        pane_id: paneId,
        message: "Ejecutable CLI 'claude' no encontrado en PATH.",
        manual_command: MANUAL_CMD,
        mcp_config: MCP_CONFIG
      }))
    });

    render(<SwarmTerminalMatrix api={api} project={PROJECT} />);
    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('pane-start-btn-pane_2'));

    const paneCard = await screen.findByTestId('pane-degraded-card-pane_2');
    expect(paneCard.textContent).toContain('RF-STM-12');
    expect(paneCard.textContent).toContain('CLI_BINARY_NOT_FOUND');
    expect(paneCard.textContent).toContain(MANUAL_CMD);
    expect(paneCard.textContent).toContain('mcpServers');
    expect(screen.getByTestId('pane-status-pane_2').textContent).toBe('Degradado');
  });

  it('integra la pestaña Swarm Matrix en AppHeader y renderiza SwarmTerminalMatrix en ProjectWorkspace', async () => {
    const api = {
      ...makeMatrixApi(),
      get_kanban_board: vi.fn(async () => ({ success: true, cards: [] })),
      get_okf_tags_index: vi.fn(async () => ({ success: true, tags: {} })),
      subscribe_project_events: vi.fn(async () => ({ success: true })),
      poll_project_events: vi.fn(async () => ({ success: true, events: [] }))
    };

    const onTabChange = vi.fn();
    const { rerender } = render(
      <>
        <AppHeader
          theme="dark"
          onToggleTheme={vi.fn()}
          onOpenSettings={vi.fn()}
          activeProject={PROJECT}
          onBackToProjects={vi.fn()}
          activeTab="kanban"
          onTabChange={onTabChange}
          githubStatus={{ authenticated: false }}
          projectsCount={1}
        />
        <ProjectWorkspace
          project={PROJECT}
          activeTab="terminals"
          onTabChange={onTabChange}
          onBack={vi.fn()}
          onNewTask={vi.fn()}
          api={api}
        />
      </>
    );

    const tabBtn = screen.getByTestId('tab-terminals');
    expect(tabBtn.textContent).toContain('Swarm Matrix');
    fireEvent.click(tabBtn);
    expect(onTabChange).toHaveBeenCalledWith('terminals');

    expect(await screen.findByTestId('swarm-terminal-matrix')).toBeTruthy();
    expect(screen.getByTestId('workspace-search-input').getAttribute('placeholder')).toContain('Swarm Matrix');

    rerender(
      <>
        <AppHeader
          theme="dark"
          onToggleTheme={vi.fn()}
          onOpenSettings={vi.fn()}
          activeProject={PROJECT}
          onBackToProjects={vi.fn()}
          activeTab="terminals"
          onTabChange={onTabChange}
          githubStatus={{ authenticated: false }}
          projectsCount={1}
        />
        <ProjectWorkspace
          project={PROJECT}
          activeTab="terminals"
          onTabChange={onTabChange}
          onBack={vi.fn()}
          onNewTask={vi.fn()}
          api={api}
        />
      </>
    );
    expect(screen.getByTestId('tab-terminals').getAttribute('aria-pressed')).toBe('true');
  });
});
