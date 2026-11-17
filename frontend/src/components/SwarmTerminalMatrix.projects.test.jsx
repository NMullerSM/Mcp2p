// @vitest-environment jsdom
// D7 (Ola 2): al cambiar de proyecto, las terminales de los demas proyectos siguen vivas pero ocultas.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup } from '@testing-library/react';
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

const OTHER = { project_uuid: 'proj_matrix_2', name: 'Otro proyecto' };

async function openCli(cliId, paneId) {
  fireEvent.click(await screen.findByTestId(`cli-open-${cliId}`));
  return screen.findByTestId(`terminal-pane-${paneId}`);
}

function isHidden(paneId) {
  return screen.getByTestId(`terminal-pane-${paneId}`).getAttribute('data-hidden') === 'true';
}

describe('SwarmTerminalMatrix: terminales por proyecto (D7)', () => {
  it('al cambiar de proyecto oculta (sin cerrar ni desmontar) las terminales del anterior y las recupera al volver', async () => {
    const api = makeMatrixApi();
    const { rerenderMatrix } = renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('gemini', 'pane_2');
    const node1 = screen.getByTestId('terminal-pane-pane_1');

    rerenderMatrix({ project: OTHER });
    // Siguen montadas (mismo nodo DOM) pero ocultas; nadie las cierra.
    expect(screen.getByTestId('terminal-pane-pane_1')).toBe(node1);
    expect(isHidden('pane_1')).toBe(true);
    expect(isHidden('pane_2')).toBe(true);
    expect(api.pty_close_pane).not.toHaveBeenCalled();
    expect(screen.queryByTestId('active-row-pane_1')).toBeNull();
    expect(screen.getByTestId('hidden-panes-count').textContent).toContain('2 terminales de otros proyectos');
    expect(screen.getByTestId('open-count').textContent).toBe('2 de 4 terminales abiertas');
    // La matriz del proyecto nuevo arranca vacia, con la invitacion a abrir una terminal.
    expect(screen.getByTestId('matrix-grid').getAttribute('data-layout')).toBe('1x1');
    expect(screen.getByTestId('terminal-pane-empty-0')).toBeTruthy();

    rerenderMatrix({ project: PROJECT });
    expect(isHidden('pane_1')).toBe(false);
    expect(isHidden('pane_2')).toBe(false);
    expect(screen.getByTestId('terminal-pane-pane_1')).toBe(node1);
    expect(screen.queryByTestId('hidden-panes-count')).toBeNull();
  });

  it('una terminal nueva en el otro proyecto usa una ranura libre y se liga a ese proyecto', async () => {
    const api = makeMatrixApi();
    const { rerenderMatrix } = renderMatrix(api);
    await openCli('claude', 'pane_1');

    rerenderMatrix({ project: OTHER });
    await openCli('gemini', 'pane_2');
    const call = api.pty_spawn_pane.mock.calls.at(-1);
    expect(call[0]).toBe('pane_2');
    expect(call[9]).toBe(OTHER.project_uuid);
    expect(isHidden('pane_1')).toBe(true);
    expect(isHidden('pane_2')).toBe(false);
    expect(screen.getByTestId('active-row-pane_2')).toBeTruthy();
    expect(screen.queryByTestId('active-row-pane_1')).toBeNull();
  });

  it('las terminales que el backend marca de otro proyecto llegan ocultas; las sin proyecto se ven siempre', async () => {
    const api = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: false, hub_url: null, hub_running: false,
        panes: [
          { pane_id: 'pane_1', node_uuid: 'a1', status: 'active', cli_profile: 'claude', project_uuid: PROJECT.project_uuid },
          { pane_id: 'pane_2', node_uuid: 'b2', status: 'active', cli_profile: 'claude', project_uuid: OTHER.project_uuid }
        ],
        sessions: [
          { pane_id: 'pane_1', session_id: 's1', alive: true, cli_profile: 'claude' },
          { pane_id: 'pane_2', session_id: 's2', alive: true, cli_profile: 'claude' },
          { pane_id: 'pane_3', session_id: 's3', alive: true, cli_profile: 'custom_shell' }
        ]
      }))
    });
    renderMatrix(api);
    await screen.findByTestId('terminal-pane-pane_2');
    expect(isHidden('pane_1')).toBe(false);
    expect(isHidden('pane_2')).toBe(true);
    expect(isHidden('pane_3')).toBe(false); // instalador o panel sin proyecto
    expect(screen.getByTestId('hidden-panes-count').textContent).toContain('1 terminal de otro proyecto');
  });
});
