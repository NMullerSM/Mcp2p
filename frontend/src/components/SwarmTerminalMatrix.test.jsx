// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import React from 'react';
import { AppHeader } from './AppHeader';
import { ProjectWorkspace } from './ProjectWorkspace';
import { resetPaneSessionMemory } from './terminals/terminalModel';
import {
  DEFAULT_CLIS,
  MANUAL_CMD,
  MCP_CONFIG,
  PROJECT,
  makeCatalogResponse,
  makeCli,
  makeMatrixApi,
  notInstalled,
  renderMatrix
} from './terminals/terminalTestKit';

afterEach(() => {
  cleanup();
  resetPaneSessionMemory();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

async function openCli(cliId, paneId) {
  fireEvent.click(await screen.findByTestId(`cli-open-${cliId}`));
  return screen.findByTestId(`terminal-pane-${paneId}`);
}

describe('SwarmTerminalMatrix: paneles = solo terminal', () => {
  it('sin terminales muestra un unico panel vacio con una invitacion minima y sin el encabezado antiguo', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalledTimes(1));

    const grid = screen.getByTestId('matrix-grid');
    expect(grid.getAttribute('data-layout')).toBe('1x1');
    expect(screen.getAllByTestId(/^terminal-pane-empty-/)).toHaveLength(1);
    expect(screen.getByTestId('matrix-empty-hint').textContent).toMatch(/pestaña lateral/);

    // Nada del chrome antiguo en la pantalla.
    expect(grid.textContent).not.toMatch(/Swarm Terminal Matrix|Actualizar|Degradado|Libre|Iniciar|Foco/);
    expect(screen.queryByTestId('matrix-header-bar')).toBeNull();
    expect(screen.queryByTestId('layout-btn-1x1')).toBeNull();
    expect(screen.queryByTestId('layout-btn-2x1')).toBeNull();
    expect(screen.queryByTestId('layout-btn-2x2')).toBeNull();
  });

  it('un panel con CLI corriendo muestra solo el logotipo y la carpeta de trabajo, sin metadatos ni controles', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);

    fireEvent.change(await screen.findByTestId('launch-cwd-input'), { target: { value: '/home/u/mi-proyecto' } });
    const pane = await openCli('claude', 'pane_1');

    const logo = within(pane).getByTestId('pane-logo-pane_1');
    expect(logo.getAttribute('data-cli')).toBe('claude');
    expect(logo.querySelector('svg')).toBeTruthy();
    expect(within(pane).getByTestId('pane-cwd-pane_1').textContent).toBe('/home/u/mi-proyecto');

    // Sin rama, PID, RSS, estado, botones ni campos de entrada dentro del panel.
    expect(pane.querySelector('button:not([data-testid="tty-detected-url"])')).toBeNull();
    expect(pane.querySelector('input, select, textarea')).toBeNull();
    expect(within(pane).queryByTestId('pane-status-pane_1')).toBeNull();
    expect(within(pane).queryByTestId('pane-pid-pane_1')).toBeNull();
    expect(within(pane).queryByTestId('pane-rss-pane_1')).toBeNull();
    expect(pane.textContent).not.toMatch(/PID|MB|Activo|Pane 1|rama|branch/i);
  });

  it('la terminal enfocada tiene resplandor; las demas no', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('gemini', 'pane_2');

    const p1 = screen.getByTestId('terminal-pane-pane_1');
    const p2 = screen.getByTestId('terminal-pane-pane_2');
    expect(p2.getAttribute('data-focused')).toBe('true');
    expect(p2.style.boxShadow).toContain('rgba(');
    expect(p1.getAttribute('data-focused')).toBe('false');
    expect(p1.style.boxShadow).toBe('none');

    fireEvent.click(p1);
    expect(p1.getAttribute('data-focused')).toBe('true');
    expect(document.activeElement).toBe(screen.getByTestId('pane-output-pane_1'));
  });

  it('escribe directo en la terminal: teclas, Ctrl+letra, pegado, Ctrl+C con y sin seleccion', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    const pre = screen.getByTestId('pane-output-pane_1');

    fireEvent.keyDown(pre, { key: 'x' });
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', 'x', true));
    fireEvent.keyDown(pre, { key: 'Enter' });
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\r', true));
    fireEvent.keyDown(pre, { key: 'ArrowUp' });
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\x1b[A', true));
    fireEvent.keyDown(pre, { key: 'd', ctrlKey: true });
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\x04', true));

    // Ctrl+C sin texto seleccionado interrumpe el proceso.
    fireEvent.keyDown(pre, { key: 'c', ctrlKey: true });
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\x03', true));

    // Con texto seleccionado deja copiar y no envia nada.
    api.pty_write_input.mockClear();
    vi.spyOn(window, 'getSelection').mockReturnValue({ toString: () => 'seleccion' });
    fireEvent.keyDown(pre, { key: 'c', ctrlKey: true });
    expect(api.pty_write_input).not.toHaveBeenCalled();

    // Pegado multilinea: saltos de linea como Enter.
    fireEvent.paste(pre, { clipboardData: { getData: () => 'echo hola\nls' } });
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', 'echo hola\rls', true));
  });

  it('hace polling incremental de pty_poll_output y escribe la salida como texto, nunca como HTML', async () => {
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async (paneId, sinceSeq = 0) => ({
        success: true,
        pane_id: paneId,
        alive: true,
        paused: false,
        rss_mb: 10,
        next_seq: 1,
        truncated: false,
        text: sinceSeq === 0 ? '<img src=x onerror=alert(1)>\x1b[32m verde\x1b[0m\n' : ''
      }))
    });
    renderMatrix(api);
    const pane = await openCli('claude', 'pane_1');

    await waitFor(() => expect(api.pty_poll_output).toHaveBeenCalledWith('pane_1', 0));
    await waitFor(() => expect(pane.textContent).toContain('<img src=x onerror=alert(1)> verde'));
    expect(pane.querySelector('img')).toBeNull();
    await waitFor(() => expect(api.pty_poll_output).toHaveBeenCalledWith('pane_1', 1));
  });

  it('protege las URLs de la salida con un dialogo de confirmacion explicito antes de abrirlas', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');

    const urlBtn = await screen.findByTestId('tty-detected-url');
    expect(urlBtn.textContent).toBe('https://docs.example.com/auth');
    fireEvent.click(urlBtn);
    expect(openSpy).not.toHaveBeenCalled();

    const modal = await screen.findByTestId('external-url-confirm-modal');
    expect(modal.textContent).toContain('https://docs.example.com/auth');
    fireEvent.click(screen.getByTestId('external-url-confirm-btn'));
    expect(openSpy).toHaveBeenCalledWith('https://docs.example.com/auth', '_blank', 'noopener,noreferrer');
  });

  it('un proceso terminado deja su salida y el codigo de salida dentro del terminal', async () => {
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async () => ({
        success: true, alive: false, paused: false, rss_mb: 0, next_seq: 2, truncated: false,
        text: 'adios\n', exit_code: 3
      }))
    });
    renderMatrix(api);
    const pane = await openCli('claude', 'pane_1');
    await waitFor(() => expect(within(pane).getByTestId('pane-exit-pane_1').textContent).toContain('código 3'));
    expect(pane.textContent).toContain('adios');
    expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Finalizado');
  });

  it('el filtro de busqueda del espacio de trabajo oculta las terminales que no coinciden', async () => {
    const api = makeMatrixApi();
    const { rerenderMatrix } = renderMatrix(api, { searchQuery: '' });
    await openCli('claude', 'pane_1');
    await openCli('gemini', 'pane_2');

    rerenderMatrix({ searchQuery: 'gemini' });
    expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull();
    expect(screen.getByTestId('terminal-pane-pane_2')).toBeTruthy();

    rerenderMatrix({ searchQuery: 'zzz-nada' });
    expect(screen.getByTestId('matrix-grid').textContent).toContain('Ninguna terminal coincide');
  });

  it('al volver a la pestana recupera las sesiones vivas del backend con su carpeta de trabajo', async () => {
    const api = makeMatrixApi();
    const first = renderMatrix(api);
    fireEvent.change(await screen.findByTestId('launch-cwd-input'), { target: { value: '/srv/app' } });
    await openCli('claude', 'pane_1');
    first.unmount();

    const api2 = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: false, hub_url: 'http://127.0.0.1:51234/mcp', hub_running: true,
        panes: [{ pane_id: 'pane_1', node_uuid: 'pane_1_abcd1234', cli_profile: 'claude', status: 'active', pid: 4242 }],
        sessions: [{
          pane_id: 'pane_1', session_id: 'pane_1-sess1', cli_profile: 'claude', role: 'implementer',
          node_uuid: 'pane_1_abcd1234', pid: 4242, alive: true, paused: false, rss_mb: 30, cols: 100, rows: 28
        }]
      }))
    });
    renderMatrix(api2);
    const pane = await screen.findByTestId('terminal-pane-pane_1');
    expect(within(pane).getByTestId('pane-cwd-pane_1').textContent).toBe('/srv/app');
    expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Activo');
  });
});

describe('SwarmTerminalMatrix: layout dinamico', () => {
  it.each([
    [1, '1x1', 0],
    [2, '2x1', 0],
    [3, '2x2', 1],
    [4, '2x2', 0],
    [5, '3x2', 1],
    [6, '3x2', 0]
  ])('con %i terminales abiertas la cuadricula es %s y sobran %i paneles vacios', async (count, layoutId, empties) => {
    const api = makeMatrixApi({
      pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse(DEFAULT_CLIS, { max_panes: 6 }))
    });
    renderMatrix(api);
    for (let n = 1; n <= count; n += 1) {
      await openCli('custom_shell', `pane_${n}`);
    }

    const grid = screen.getByTestId('matrix-grid');
    expect(grid.getAttribute('data-layout')).toBe(layoutId);
    expect(screen.getAllByTestId(/^terminal-pane-pane_\d$/)).toHaveLength(count);
    expect(screen.queryAllByTestId(/^terminal-pane-empty-/)).toHaveLength(empties);
    // El panel vacio es solo la superficie negra: sin texto, y sin invitacion si ya hay terminales.
    screen.queryAllByTestId(/^terminal-pane-empty-/).forEach((el) => expect(el.textContent).toBe(''));
    expect(screen.queryByTestId('matrix-empty-hint')).toBeNull();
  });

  it('al cerrar una terminal la cuadricula se reduce', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('gemini', 'pane_2');
    expect(screen.getByTestId('matrix-grid').getAttribute('data-layout')).toBe('2x1');

    fireEvent.click(screen.getByTestId('pane-close-btn-pane_2'));
    await waitFor(() => expect(api.pty_close_pane).toHaveBeenCalledWith('pane_2'));
    await waitFor(() => expect(screen.queryByTestId('terminal-pane-pane_2')).toBeNull());
    expect(screen.getByTestId('matrix-grid').getAttribute('data-layout')).toBe('1x1');
  });

  it('respeta el tope max_panes del backend: con 4 abiertas no lanza una quinta y avisa', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    for (let n = 1; n <= 4; n += 1) await openCli('custom_shell', `pane_${n}`);

    api.pty_spawn_pane.mockClear();
    fireEvent.click(screen.getByTestId('cli-open-claude'));
    const notice = await screen.findByTestId('slider-notice');
    expect(notice.getAttribute('data-code')).toBe('PANE_LIMIT_REACHED');
    expect(screen.getByTestId('slider-notice-title').textContent).toMatch(/Ya hay 4 terminales/);
    expect(api.pty_spawn_pane).not.toHaveBeenCalled();
    expect(screen.queryByTestId('terminal-pane-pane_5')).toBeNull();
  });
});

describe('SwarmTerminalMatrix: controles en el slider', () => {
  it('lista los CLIs del catalogo con instalado/no instalado, version y ruta', async () => {
    const api = makeMatrixApi({
      pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse([
        makeCli('claude', { version: '2.1.0', resolved_path: '/home/u/.local/bin/claude' }),
        notInstalled('gemini'),
        makeCli('custom_shell')
      ]))
    });
    renderMatrix(api);

    expect((await screen.findByTestId('cli-status-claude')).textContent).toBe('Instalado');
    expect(screen.getByTestId('cli-version-claude').textContent).toBe('2.1.0');
    expect(screen.getByTestId('cli-path-claude').textContent).toBe('/home/u/.local/bin/claude');
    expect(screen.getByTestId('cli-status-gemini').textContent).toBe('No instalado');
    expect(screen.getByTestId('cli-open-gemini').disabled).toBe(true);
    expect(screen.getByTestId('cli-open-claude').disabled).toBe(false);
  });

  it('Abrir lanza pty_spawn_pane con el perfil, el rol y la carpeta de trabajo escrita, y muestra el hub', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);

    fireEvent.change(await screen.findByTestId('launch-cwd-input'), { target: { value: ' /tmp/proyecto ' } });
    fireEvent.change(screen.getByTestId('launch-role-select'), { target: { value: 'reviewer' } });
    await openCli('gemini', 'pane_1');

    expect(api.pty_spawn_pane).toHaveBeenCalledWith('pane_1', 'gemini', 'reviewer', null, 100, 28, true, '/tmp/proyecto');
    expect(screen.getByTestId('hub-status-badge').textContent).toContain('Hub activo: http://127.0.0.1:51234/mcp');
    expect(screen.getByTestId('pane-cwd-pane_1').textContent).toBe('/tmp/proyecto');
  });

  it('sin carpeta escrita envia cwd null para que el backend use la del proyecto', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    expect(api.pty_spawn_pane.mock.calls[0][7]).toBeNull();
  });

  it('cada terminal activa se controla desde el slider: RSS, pausa, reanudar, tamano y cerrar', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');

    expect(screen.getByTestId('pane-pid-pane_1').textContent).toContain('4242');
    expect(screen.getByTestId('pane-rss-pane_1').textContent).toContain('24.5 MB');
    expect(screen.getByTestId('pane-node-pane_1').textContent).toContain('pane_1_abcd1');
    expect(screen.getByTestId('pane-registry-pane_1').textContent).toBe('active');
    expect(screen.getByTestId('open-count').textContent).toBe('1 de 4 terminales abiertas');

    fireEvent.click(screen.getByTestId('pane-pause-btn-pane_1'));
    await waitFor(() => expect(api.pty_pause_pane).toHaveBeenCalledWith('pane_1', true));
    expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Pausado');
    expect(screen.getByTestId('pane-pause-btn-pane_1').textContent).toContain('Reanudar');

    fireEvent.click(screen.getByTestId('pane-pause-btn-pane_1'));
    await waitFor(() => expect(api.pty_pause_pane).toHaveBeenCalledWith('pane_1', false));
    await waitFor(() => expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Activo'));

    fireEvent.change(screen.getByTestId('size-select-pane_1'), { target: { value: '120x36' } });
    await waitFor(() => expect(api.pty_resize_pane).toHaveBeenCalledWith('pane_1', 120, 36));

    fireEvent.click(screen.getByTestId('pane-close-btn-pane_1'));
    await waitFor(() => expect(api.pty_close_pane).toHaveBeenCalledWith('pane_1'));
    await waitFor(() => expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull());
    expect(screen.getByTestId('active-empty')).toBeTruthy();
  });

  it('seleccionar una terminal activa en el slider la enfoca', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('gemini', 'pane_2');
    expect(screen.getByTestId('terminal-pane-pane_2').getAttribute('data-focused')).toBe('true');

    fireEvent.click(screen.getByTestId('pane-focus-btn-pane_1'));
    expect(screen.getByTestId('terminal-pane-pane_1').getAttribute('data-focused')).toBe('true');
    expect(screen.getByTestId('terminal-pane-pane_2').getAttribute('data-focused')).toBe('false');
    expect(document.activeElement).toBe(screen.getByTestId('pane-output-pane_1'));
  });

  it('el selector de maximo llama a pty_set_max_panes y actualiza el contador', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    const select = await screen.findByTestId('max-panes-select');
    expect(select.value).toBe('4');
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['1', '2', '3', '4', '5', '6']);

    fireEvent.change(select, { target: { value: '6' } });
    await waitFor(() => expect(api.pty_set_max_panes).toHaveBeenCalledWith(6));
    await waitFor(() => expect(screen.getByTestId('open-count').textContent).toBe('0 de 6 terminales abiertas'));
  });

  it('Actualizar estado vuelve a leer el estado del enjambre y el catalogo', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await waitFor(() => expect(api.pty_list_cli_catalog).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId('matrix-refresh-btn'));
    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.pty_list_cli_catalog).toHaveBeenCalledTimes(2));
  });

  it('la conexion manual (comando y mcpServers) se copia desde el slider', async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const api = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: false, hub_url: null, hub_running: false, panes: [], sessions: [],
        manual_command: MANUAL_CMD, mcp_config: MCP_CONFIG
      }))
    });
    renderMatrix(api);
    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('toggle-manual-instructions-btn'));
    const card = await screen.findByTestId('matrix-manual-card');
    expect(card.textContent).toContain(MANUAL_CMD);
    expect(card.textContent).toContain('mcpServers');
    fireEvent.click(screen.getByTestId('matrix-manual-card-cmd-copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(MANUAL_CMD));
  });
});

describe('SwarmTerminalMatrix: errores con causa y accion (sin "degradado" generico)', () => {
  const CASES = [
    {
      name: 'CLI_BINARY_NOT_FOUND con instalacion disponible',
      res: { success: false, degraded: false, code: 'CLI_BINARY_NOT_FOUND', cli_profile: 'claude', install_available: true, message: 'No se encontró el ejecutable de Claude' },
      title: /No se encontró Claude/,
      hint: /instalar/i
    },
    {
      name: 'CLI_BINARY_NOT_FOUND sin instalacion',
      res: { success: false, degraded: false, code: 'CLI_BINARY_NOT_FOUND', cli_profile: 'claude', install_available: false, install_missing_requirements: ['npm'] },
      title: /No se encontró Claude/,
      hint: /npm/
    },
    {
      name: 'CLI_PROFILE_UNKNOWN',
      res: { success: false, degraded: false, code: 'CLI_PROFILE_UNKNOWN', known_profiles: ['claude', 'codex'] },
      title: /Perfil de CLI no reconocido/,
      hint: /claude, codex/
    },
    {
      name: 'PANE_LIMIT_REACHED / pane_out_of_range',
      res: { success: false, degraded: false, code: 'PANE_LIMIT_REACHED', reason: 'pane_out_of_range', max_panes: 4 },
      title: /Panel fuera de rango/,
      hint: /pane_4/
    },
    {
      name: 'PANE_LIMIT_REACHED / max_panes',
      res: { success: false, degraded: false, code: 'PANE_LIMIT_REACHED', reason: 'max_panes', max_panes: 4 },
      title: /Ya hay 4 terminales abiertas/,
      hint: /sube el máximo/i
    },
    {
      name: 'PANE_LIMIT_REACHED / low_memory',
      res: { success: false, degraded: false, code: 'PANE_LIMIT_REACHED', reason: 'low_memory', message: 'Memoria disponible insuficiente' },
      title: /Memoria disponible insuficiente/,
      hint: /libera memoria/i
    },
    {
      name: 'INVALID_CWD',
      res: { success: false, degraded: false, code: 'INVALID_CWD', message: 'La carpeta de trabajo no existe o no es un directorio: /nada' },
      title: /Carpeta de trabajo no válida/,
      hint: /ruta absoluta/
    },
    {
      name: 'PREREQUISITE_MISSING',
      res: { success: false, degraded: false, code: 'PREREQUISITE_MISSING', missing: ['npm'] },
      title: /Faltan herramientas previas/,
      hint: /npm/
    },
    {
      name: 'TERMINAL_PLATFORM_UNSUPPORTED (Windows)',
      res: { success: false, degraded: true, code: 'TERMINAL_PLATFORM_UNSUPPORTED', manual_command: MANUAL_CMD, mcp_config: MCP_CONFIG },
      title: /aún no están disponibles en Windows/,
      hint: /terminal propia/,
      manual: true
    },
    {
      name: 'TERMINAL_ADAPTER_DEGRADED',
      res: { success: false, degraded: true, code: 'TERMINAL_ADAPTER_DEGRADED', manual_command: MANUAL_CMD },
      title: /conector de terminales no está disponible/,
      hint: /Actualizar estado/,
      manual: true
    },
    {
      name: 'PTY_UNAVAILABLE',
      res: { success: false, degraded: true, code: 'PTY_UNAVAILABLE', manual_command: MANUAL_CMD },
      title: /conector de terminales no está disponible/,
      hint: /comando manual/,
      manual: true
    }
  ];

  it.each(CASES)('$name se explica en el slider y no abre ningun panel', async ({ res, title, hint, manual }) => {
    const api = makeMatrixApi({ pty_spawn_pane: vi.fn(async () => res) });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-open-claude'));

    const notice = await screen.findByTestId('slider-notice');
    expect(notice.getAttribute('data-code')).toBe(res.code);
    expect(screen.getByTestId('slider-notice-title').textContent).toMatch(title);
    expect(screen.getByTestId('slider-notice-hint').textContent).toMatch(hint);
    expect(notice.textContent.toLowerCase()).not.toContain('modo degradado');
    if (manual) expect(screen.getByTestId('slider-notice-manual-cmd').textContent).toBe(MANUAL_CMD);

    // Nada de tarjetas dentro de paneles: no hay terminal abierta y el panel sigue vacio.
    expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull();
    expect(screen.getByTestId('terminal-pane-empty-0').textContent).toMatch(/pestaña lateral/);
    // Aviso discreto fuera del slider.
    expect(screen.getByTestId('matrix-notice').getAttribute('data-code')).toBe(res.code);
  });

  it('el aviso discreto se descarta y "Ver detalle" abre el slider', async () => {
    const api = makeMatrixApi({
      pty_spawn_pane: vi.fn(async () => ({ success: false, degraded: false, code: 'INVALID_CWD', message: 'x' }))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-open-claude'));
    await screen.findByTestId('matrix-notice');

    fireEvent.click(screen.getByTestId('matrix-notice-open'));
    expect(screen.getByTestId('view-slider-tab').getAttribute('aria-expanded')).toBe('true');
    expect(screen.queryByTestId('matrix-notice')).toBeNull();

    fireEvent.click(screen.getByTestId('slider-notice-dismiss'));
    expect(screen.queryByTestId('slider-notice')).toBeNull();
  });

  it('el estado del conector se explica con su causa en el slider (Windows y backend caido)', async () => {
    const unsupported = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: true, platform_unsupported: true, code: 'TERMINAL_PLATFORM_UNSUPPORTED',
        panes: [], sessions: [], manual_command: MANUAL_CMD, mcp_config: MCP_CONFIG
      })),
      pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse(DEFAULT_CLIS, { platform: 'win32', terminal_platform_supported: false }))
    });
    const first = renderMatrix(unsupported);
    const status = await screen.findByTestId('status-notice');
    expect(status.getAttribute('data-code')).toBe('TERMINAL_PLATFORM_UNSUPPORTED');
    expect(screen.getByTestId('status-notice-manual-cmd').textContent).toBe(MANUAL_CMD);
    expect(screen.getByTestId('cli-open-claude').disabled).toBe(true);
    expect(screen.getByTestId('hub-status-badge').textContent).toMatch(/no disponibles en este sistema/);
    first.unmount();

    const down = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: true, panes: [], sessions: [], manual_command: MANUAL_CMD, mcp_config: MCP_CONFIG
      }))
    });
    renderMatrix(down);
    const status2 = await screen.findByTestId('status-notice');
    expect(status2.getAttribute('data-code')).toBe('TERMINAL_ADAPTER_DEGRADED');
    expect(screen.getByTestId('hub-status-badge').textContent).toMatch(/Conector de terminales no disponible/);
  });
});

describe('SwarmTerminalMatrix en el espacio de trabajo', () => {
  it('integra la pestana Swarm Matrix en AppHeader y renderiza SwarmTerminalMatrix en ProjectWorkspace con su slider', async () => {
    const api = {
      ...makeMatrixApi(),
      get_kanban_board: vi.fn(async () => ({ success: true, cards: [] })),
      get_okf_tags_index: vi.fn(async () => ({ success: true, tags: {} })),
      subscribe_project_events: vi.fn(async () => ({ success: true })),
      poll_project_events: vi.fn(async () => ({ success: true, events: [] }))
    };

    const onTabChange = vi.fn();
    const header = (activeTab) => (
      <AppHeader
        theme="dark"
        onToggleTheme={vi.fn()}
        onOpenSettings={vi.fn()}
        activeProject={PROJECT}
        onBackToProjects={vi.fn()}
        activeTab={activeTab}
        onTabChange={onTabChange}
        githubStatus={{ authenticated: false }}
        projectsCount={1}
      />
    );
    const workspace = (
      <ProjectWorkspace
        project={PROJECT}
        activeTab="terminals"
        onTabChange={onTabChange}
        onBack={vi.fn()}
        onNewTask={vi.fn()}
        api={api}
      />
    );
    const { rerender } = render(<>{header('kanban')}{workspace}</>);

    const tabBtn = screen.getByTestId('tab-terminals');
    expect(tabBtn.textContent).toContain('Swarm Matrix');
    fireEvent.click(tabBtn);
    expect(onTabChange).toHaveBeenCalledWith('terminals');

    expect(await screen.findByTestId('swarm-terminal-matrix')).toBeTruthy();
    expect(screen.getByTestId('workspace-search-input').getAttribute('placeholder')).toContain('Swarm Matrix');

    // El control de terminales vive en el slider de la vista.
    expect(screen.getByTestId('terminals-slider')).toBeTruthy();
    expect(within(screen.getByTestId('view-slider-panel')).getByTestId('terminals-slider-controls')).toBeTruthy();
    expect(screen.getByTestId('view-slider-tab').getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(screen.getByTestId('view-slider-tab'));
    expect(screen.getByTestId('view-slider-tab').getAttribute('aria-expanded')).toBe('true');
    expect(await screen.findByTestId('cli-open-claude')).toBeTruthy();

    rerender(<>{header('terminals')}{workspace}</>);
    expect(screen.getByTestId('tab-terminals').getAttribute('aria-pressed')).toBe('true');
  });
});
