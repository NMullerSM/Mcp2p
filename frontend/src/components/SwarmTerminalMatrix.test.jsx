// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import React from 'react';
import { AppHeader } from './AppHeader';
import { ProjectWorkspace } from './ProjectWorkspace';
import { resetPaneSessionMemory } from './terminals/terminalModel';
import { createUrlLinkProvider } from './terminals/terminalLinks';
import {
  DEFAULT_CLIS,
  MANUAL_CMD,
  MCP_CONFIG,
  PROJECT,
  makeCatalogResponse,
  makeCli,
  makeMatrixApi,
  Terminal,
  installResizeObserverDouble,
  notInstalled,
  renderMatrix,
  resetTerminalTestState,
  termOf
} from './terminals/terminalTestKit';

// jsdom no renderiza xterm: se sustituye por un doble que registra lo que se le escribe.
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

describe('SwarmTerminalMatrix: panel vacio clicable y Auto-Relay en el slider', () => {
  it('el panel vacio es un boton "Agregar terminal" que abre el slider lateral', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalledTimes(1));

    const slot = screen.getByTestId('terminal-pane-empty-0');
    expect(slot.tagName).toBe('BUTTON');
    expect(slot.getAttribute('aria-label')).toBe('Agregar terminal');
    expect(screen.getByTestId('terminals-slider').getAttribute('data-state')).toBe('closed');

    fireEvent.click(slot);
    expect(screen.getByTestId('terminals-slider').getAttribute('data-state')).toBe('open');
  });

  it('el toggle de Auto-Relay de cada terminal vive dentro del panel del slider y ya no hay barra de controles en la vista', async () => {
    const api = makeMatrixApi({ pty_set_auto_relay: vi.fn(async () => ({ success: true })) });
    renderMatrix(api);
    // D2: ya no hay interruptor global ni seccion larga; hay interruptor + InfoHint por terminal abierta.
    expect(screen.queryByTestId('auto-relay-section')).toBeNull();
    expect(screen.queryByTestId('auto-relay-toggle')).toBeNull();
    await openCli('claude', 'pane_1');
    const toggle = await screen.findByTestId('pane-auto-relay-toggle-pane_1');
    expect(screen.getByTestId('pane-auto-relay-hint-pane_1')).toBeTruthy();

    expect(screen.queryByTestId('matrix-controls-bar')).toBeNull();
    expect(within(screen.getByTestId('view-slider-panel')).getByTestId('pane-auto-relay-toggle-pane_1')).toBe(toggle);
    expect(screen.getByTestId('matrix-grid').contains(toggle)).toBe(false);
  });
});

describe('SwarmTerminalMatrix: paneles = solo terminal', () => {
  it('sin terminales muestra un unico panel vacio con una invitacion minima y sin el encabezado antiguo', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await waitFor(() => expect(api.pty_get_matrix_status).toHaveBeenCalledTimes(1));

    const grid = screen.getByTestId('matrix-grid');
    expect(grid.getAttribute('data-layout')).toBe('1x1');
    expect(screen.getAllByTestId(/^terminal-pane-empty-/)).toHaveLength(1);
    expect(screen.getByTestId('matrix-empty-hint').textContent).toMatch(/Agregar terminal/);

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

    // Sin rama, PID, RSS, estado, botones ni campos de entrada dentro del panel (salvo el chip de rol en cabecera).
    expect(pane.querySelector('button:not([data-testid^="role-chip-"])')).toBeNull();
    expect(pane.querySelector('input, select, textarea:not(.xterm-helper-textarea)')).toBeNull();
    expect(within(pane).queryByTestId('pane-status-pane_1')).toBeNull();
    expect(within(pane).queryByTestId('pane-pid-pane_1')).toBeNull();
    expect(within(pane).queryByTestId('pane-rss-pane_1')).toBeNull();
    expect(pane.textContent).not.toMatch(/PID|MB|Activo|Pane 1|rama|branch/i);
  });

  it('la terminal enfocada tiene resplandor; las demas no', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('codex', 'pane_2');

    const p1 = screen.getByTestId('terminal-pane-pane_1');
    const p2 = screen.getByTestId('terminal-pane-pane_2');
    expect(p2.getAttribute('data-focused')).toBe('true');
    expect(p2.style.boxShadow).toContain('rgba(');
    expect(p1.getAttribute('data-focused')).toBe('false');
    expect(p1.style.boxShadow).toBe('none');

    fireEvent.click(p1);
    expect(p1.getAttribute('data-focused')).toBe('true');
    expect(termOf('pane_1').textarea).toBe(document.activeElement);
    expect(screen.getByTestId('pane-output-pane_1').contains(document.activeElement)).toBe(true);
  });

  it('la entrada de xterm (onData) se reenvia tal cual al PTY como entrada humana', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    const term = termOf('pane_1');

    // Teclas, secuencias de cursor y Ctrl+letra: las traduce xterm; el panel solo las reenvia.
    term.emitData('x');
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', 'x', true));
    term.emitData('\r');
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\r', true));
    term.emitData('\x1b[A');
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\x1b[A', true));
    term.emitData('\x04');
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', '\x04', true));
    // Pegado (xterm ya lo entrega como datos, con saltos de linea como Enter).
    term.emitData('echo hola\rls');
    await waitFor(() => expect(api.pty_write_input).toHaveBeenCalledWith('pane_1', 'echo hola\rls', true));
    // Siempre con el tercer argumento (marca el tecleo como humano; solo telemetria de la GUI).
    api.pty_write_input.mock.calls.forEach((call) => expect(call[2]).toBe(true));
  });

  it('teclear deprisa: una sola escritura en vuelo por panel y el texto llega en orden (EGM-80)', async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const written = [];
    const api = makeMatrixApi({
      pty_write_input: vi.fn(async (_paneId, data) => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        // El puente real no garantiza orden entre llamadas: la primera es la mas lenta.
        await new Promise((r) => setTimeout(r, written.length === 0 ? 20 : 1));
        written.push(data);
        inFlight -= 1;
        return { success: true };
      })
    });
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    const term = termOf('pane_1');
    const text = 'echo abcdefghij\r';
    for (const ch of text) term.emitData(ch);
    await waitFor(() => expect(written.join('')).toBe(text));
    expect(maxInFlight).toBe(1);
    expect(api.pty_write_input.mock.calls.length).toBeLessThan(text.length);
    api.pty_write_input.mock.calls.forEach((call) => {
      expect(call[0]).toBe('pane_1');
      expect(call[2]).toBe(true);
    });
  });

  it('no envia nada al PTY de un proceso terminado', async () => {
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async () => ({
        success: true, alive: false, paused: false, rss_mb: 0, next_seq: 1, truncated: false, text: '', exit_code: 0
      }))
    });
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await waitFor(() => expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Finalizado'));
    termOf('pane_1').emitData('x');
    await new Promise((r) => setTimeout(r, 40));
    expect(api.pty_write_input).not.toHaveBeenCalled();
  });

  it('Ctrl+C con seleccion copia y no interrumpe; sin seleccion pasa a xterm (^C); Ctrl+V deja pegar', async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    const term = termOf('pane_1');

    // Sin seleccion: xterm procesa la tecla y manda ^C al PTY (interrumpe).
    expect(term.pressKey({ key: 'c', ctrlKey: true })).toBe(true);
    expect(writeText).not.toHaveBeenCalled();

    // Con seleccion: se copia al portapapeles y xterm NO la procesa (no llega ^C al proceso).
    term.selection = 'texto seleccionado';
    expect(term.pressKey({ key: 'c', ctrlKey: true })).toBe(false);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('texto seleccionado'));

    // Ctrl+Shift+C sin seleccion no envia nada.
    term.selection = '';
    expect(term.pressKey({ key: 'C', ctrlKey: true, shiftKey: true })).toBe(false);

    // Ctrl+V / Ctrl+Shift+V: lo resuelve el evento paste del navegador, no el manejador de teclas.
    expect(term.pressKey({ key: 'v', ctrlKey: true })).toBe(false);
    expect(term.pressKey({ key: 'V', ctrlKey: true, shiftKey: true })).toBe(false);

    // Esc, flechas y demas son de la terminal: nada se intercepta.
    expect(term.pressKey({ key: 'Escape' })).toBe(true);
    expect(term.pressKey({ key: 'ArrowUp' })).toBe(true);
    expect(term.pressKey({ key: 'd', ctrlKey: true })).toBe(true);
    expect(api.pty_write_input).not.toHaveBeenCalled();
  });

  it('hace polling incremental de pty_poll_output y alimenta a xterm con el texto tal cual (con ANSI), nunca como HTML', async () => {
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async (paneId, sinceSeq = 0) => ({
        success: true,
        pane_id: paneId,
        alive: true,
        paused: false,
        rss_mb: 10,
        since_seq: sinceSeq,
        next_seq: 1,
        truncated: false,
        text: sinceSeq === 0 ? '<img src=x onerror=alert(1)>\x1b[32m verde\x1b[0m\n' : ''
      }))
    });
    renderMatrix(api);
    const pane = await openCli('claude', 'pane_1');

    await waitFor(() => expect(api.pty_poll_output).toHaveBeenCalledWith('pane_1', 0));
    await waitFor(() => expect(termOf('pane_1').output).toContain('<img src=x onerror=alert(1)>\x1b[32m verde\x1b[0m\n'));
    // Los codigos ANSI llegan intactos a xterm (los interpreta el emulador) y nada se inyecta como DOM.
    expect(pane.querySelector('img')).toBeNull();
    expect(pane.textContent).not.toContain('onerror');
    await waitFor(() => expect(api.pty_poll_output).toHaveBeenCalledWith('pane_1', 1));
    // El texto solo se escribe una vez aunque haya muchos sondeos posteriores.
    expect(termOf('pane_1').written.filter((chunk) => chunk.includes('verde'))).toHaveLength(1);
  });

  it('consume text/next_seq y nunca el campo duplicado chunks (EGM-78)', async () => {
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async (paneId, sinceSeq = 0) => ({
        success: true, alive: true, paused: false, rss_mb: 10, since_seq: sinceSeq, next_seq: 3, truncated: false,
        chunks: [{ seq: 1, text: 'SOLO_EN_CHUNKS' }],
        text: sinceSeq === 0 ? 'salida real\n' : ''
      }))
    });
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await waitFor(() => expect(api.pty_poll_output).toHaveBeenCalledWith('pane_1', 3));
    expect(termOf('pane_1').output).toBe('salida real\n');
    expect(termOf('pane_1').output).not.toContain('SOLO_EN_CHUNKS');
  });

  it('si el backend trunca el buffer, avisa de forma discreta dentro de la terminal', async () => {
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async (paneId, sinceSeq = 0) => ({
        success: true, alive: true, paused: false, rss_mb: 10, since_seq: sinceSeq, next_seq: 9,
        truncated: sinceSeq === 0, text: sinceSeq === 0 ? 'resto\n' : ''
      }))
    });
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await waitFor(() => expect(termOf('pane_1').output).toContain('salida anterior truncada del buffer'));
    const out = termOf('pane_1').output;
    expect(out.indexOf('truncada')).toBeLessThan(out.indexOf('resto'));
    expect(out).toContain('\x1b[2m'); // gris tenue, sin llamar la atencion
  });

  it('las URLs de la terminal pasan por un dialogo de confirmacion explicito antes de abrirse', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await waitFor(() => expect(termOf('pane_1').output).toContain('docs.example.com'));

    // xterm consulta el proveedor de enlaces con la fila bajo el raton.
    const term = termOf('pane_1');
    term.lines = [{ isWrapped: false, translateToString: () => 'Agente listo en https://docs.example.com/auth.' }];
    expect(term.linkProviders).toHaveLength(1);
    let links;
    term.linkProviders[0].provideLinks(1, (found) => { links = found; });
    expect(links).toHaveLength(1);
    expect(links[0].text).toBe('https://docs.example.com/auth');

    links[0].activate({}, links[0].text);
    expect(openSpy).not.toHaveBeenCalled();
    const modal = await screen.findByTestId('external-url-confirm-modal');
    expect(modal.textContent).toContain('https://docs.example.com/auth');
    fireEvent.click(screen.getByTestId('external-url-confirm-btn'));
    expect(openSpy).toHaveBeenCalledWith('https://docs.example.com/auth', '_blank', 'noopener,noreferrer');
  });

  it('un proceso terminado deja su salida y el codigo de salida dentro de la terminal', async () => {
    const api = makeMatrixApi({
      pty_poll_output: vi.fn(async () => ({
        success: true, alive: false, paused: false, rss_mb: 0, next_seq: 2, truncated: false,
        text: 'adios\n', exit_code: 3
      }))
    });
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await waitFor(() => expect(termOf('pane_1').output).toContain('proceso terminado, código 3'));
    expect(termOf('pane_1').output.indexOf('adios')).toBeLessThan(termOf('pane_1').output.indexOf('proceso terminado'));
    // El aviso se escribe una sola vez.
    expect(termOf('pane_1').written.filter((chunk) => chunk.includes('proceso terminado'))).toHaveLength(1);
    expect(screen.getByTestId('pane-status-pane_1').textContent).toBe('Finalizado');
    expect(screen.getByTestId('active-row-pane_1').textContent).toContain('código 3');
  });

  it('el filtro de busqueda del espacio de trabajo oculta las terminales que no coinciden', async () => {
    const api = makeMatrixApi();
    const { rerenderMatrix } = renderMatrix(api, { searchQuery: '' });
    await openCli('claude', 'pane_1');
    await openCli('codex', 'pane_2');

    rerenderMatrix({ searchQuery: 'codex' });
    // Se oculta sin desmontarse (la terminal conserva su pantalla).
    expect(screen.getByTestId('terminal-pane-pane_1').getAttribute('data-hidden')).toBe('true');
    expect(screen.getByTestId('terminal-pane-pane_1').style.display).toBe('none');
    expect(screen.getByTestId('terminal-pane-pane_2').getAttribute('data-hidden')).toBe('false');

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

  it('la carpeta de trabajo que devuelve pty_get_matrix_status (cwd de la sesion) manda sobre la recordada', async () => {
    const api = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: false, hub_url: null, hub_running: false, panes: [],
        sessions: [{
          pane_id: 'pane_1', session_id: 'pane_1-sess9', cli_profile: 'codex', role: 'reviewer',
          node_uuid: 'n1', pid: 77, alive: true, paused: false, rss_mb: 12, cols: 90, rows: 30, cwd: '/desde/el/backend'
        }]
      }))
    });
    renderMatrix(api);
    const pane = await screen.findByTestId('terminal-pane-pane_1');
    expect(within(pane).getByTestId('pane-cwd-pane_1').textContent).toBe('/desde/el/backend');
    // La terminal nace con el tamano real de la sesion viva.
    expect(termOf('pane_1').cols).toBe(90);
    expect(termOf('pane_1').rows).toBe(30);
  });

  it('al reconectar a una sesion viva la misma instancia de xterm sigue y el sondeo continua desde su seq (sin repetir la salida)', async () => {
    const polls = [];
    const pollFn = vi.fn(async (paneId, sinceSeq = 0) => {
      polls.push(sinceSeq);
      return {
        success: true, alive: true, paused: false, rss_mb: 10, since_seq: sinceSeq,
        next_seq: sinceSeq === 0 ? 5 : sinceSeq, truncated: false,
        text: sinceSeq === 0 ? '\x1b[?1049hPANTALLA ALTERNA\n' : ''
      };
    });
    const api = makeMatrixApi({ pty_poll_output: pollFn });
    const first = renderMatrix(api);
    await openCli('claude', 'pane_1');
    await waitFor(() => expect(termOf('pane_1').output).toContain('PANTALLA ALTERNA'));
    const instance = termOf('pane_1');
    first.unmount();
    // Desmontar la vista NO destruye la terminal.
    expect(instance.disposed).toBe(false);

    polls.length = 0;
    const api2 = makeMatrixApi({
      pty_poll_output: pollFn,
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: false, hub_url: null, hub_running: false, panes: [],
        sessions: [{
          pane_id: 'pane_1', session_id: 'pane_1-sess1', cli_profile: 'claude', role: 'implementer',
          node_uuid: 'n1', pid: 4242, alive: true, paused: false, rss_mb: 30, cols: 100, rows: 28
        }]
      }))
    });
    renderMatrix(api2);
    await screen.findByTestId('terminal-pane-pane_1');
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    expect(Terminal.instances).toHaveLength(1);
    expect(termOf('pane_1')).toBe(instance);
    expect(polls.every((seq) => seq === 5)).toBe(true); // jamas vuelve a pedir desde 0
    expect(instance.output.match(/PANTALLA ALTERNA/g)).toHaveLength(1);
    // Y el elemento de xterm vuelve a estar colgado en el panel nuevo.
    expect(screen.getByTestId('pane-output-pane_1').contains(instance.element)).toBe(true);
  });

  it('una sesion nueva en el mismo panel empieza con una terminal limpia', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    const old = termOf('pane_1');
    fireEvent.click(screen.getByTestId('pane-close-btn-pane_1'));
    await waitFor(() => expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull());
    expect(old.disposed).toBe(true);

    await openCli('codex', 'pane_1');
    expect(termOf('pane_1')).not.toBe(old);
    expect(termOf('pane_1').disposed).toBe(false);
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
    await openCli('codex', 'pane_2');
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
        notInstalled('codex'),
        makeCli('custom_shell')
      ]))
    });
    renderMatrix(api);

    expect((await screen.findByTestId('cli-status-claude')).textContent).toBe('Instalado');
    expect(screen.getByTestId('cli-version-claude').textContent).toBe('2.1.0');
    expect(screen.getByTestId('cli-path-claude').textContent).toBe('/home/u/.local/bin/claude');
    expect(screen.getByTestId('cli-status-codex').textContent).toBe('No instalado');
    expect(screen.getByTestId('cli-open-codex').disabled).toBe(true);
    expect(screen.getByTestId('cli-open-claude').disabled).toBe(false);
  });

  it('launcher_has_no_role_select: Abrir lanza pty_spawn_pane con el perfil, rol peer y carpeta de trabajo escrita, y muestra el hub', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);

    fireEvent.change(await screen.findByTestId('launch-cwd-input'), { target: { value: ' /tmp/proyecto ' } });
    expect(screen.queryByTestId('launch-role-select')).toBeNull();
    await openCli('codex', 'pane_1');

    // Ola 2: el panel nace ligado al proyecto visible (ultimo argumento) y con rol peer por defecto.
    expect(api.pty_spawn_pane).toHaveBeenCalledWith(
      'pane_1', 'codex', 'peer', null, 100, 28, true, '/tmp/proyecto', false, PROJECT.project_uuid
    );
    expect(screen.getByTestId('hub-status-badge').textContent).toContain('Hub activo: http://127.0.0.1:51234/mcp');
    expect(screen.getByTestId('pane-cwd-pane_1').textContent).toBe('/tmp/proyecto');
    expect(screen.getByTestId('role-chip-pane_1').textContent).toContain('Par');
  });

  it('sin carpeta escrita envia cwd null para que el backend use la del proyecto', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    expect(api.pty_spawn_pane.mock.calls[0][7]).toBeNull();
  });

  it('cada terminal activa se controla desde el slider: RSS, pausa, reanudar, tamano visible y cerrar', async () => {
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

    // El tamano ya no se elige a mano: se ve el real y se ajusta solo al panel (fit).
    expect(screen.getByTestId('pane-size-pane_1').textContent).toBe('100x28');
    expect(screen.queryByTestId('size-select-pane_1')).toBeNull();

    fireEvent.click(screen.getByTestId('pane-close-btn-pane_1'));
    await waitFor(() => expect(api.pty_close_pane).toHaveBeenCalledWith('pane_1'));
    await waitFor(() => expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull());
    expect(screen.getByTestId('active-empty')).toBeTruthy();
  });

  it('seleccionar una terminal activa en el slider la enfoca', async () => {
    const api = makeMatrixApi();
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('codex', 'pane_2');
    expect(screen.getByTestId('terminal-pane-pane_2').getAttribute('data-focused')).toBe('true');

    fireEvent.click(screen.getByTestId('pane-focus-btn-pane_1'));
    expect(screen.getByTestId('terminal-pane-pane_1').getAttribute('data-focused')).toBe('true');
    expect(screen.getByTestId('terminal-pane-pane_2').getAttribute('data-focused')).toBe('false');
    expect(termOf('pane_1').textarea).toBe(document.activeElement);
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
    expect(screen.getByTestId('terminal-pane-empty-0').textContent).toMatch(/Agregar terminal/);
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

  it('cambiar de pestana NO desmonta las terminales: se ocultan, siguen vivas y el sondeo continua despacio', async () => {
    const api = {
      ...makeMatrixApi(),
      get_kanban_board: vi.fn(async () => ({ success: true, cards: [] })),
      get_okf_tags_index: vi.fn(async () => ({ success: true, tags: {} })),
      subscribe_project_events: vi.fn(async () => ({ success: true })),
      poll_project_events: vi.fn(async () => ({ success: true, events: [] }))
    };
    const workspace = (activeTab) => (
      <ProjectWorkspace
        project={PROJECT}
        activeTab={activeTab}
        onTabChange={vi.fn()}
        onBack={vi.fn()}
        onNewTask={vi.fn()}
        api={api}
      />
    );
    const { rerender } = render(workspace('terminals'));
    await openCli('claude', 'pane_1');
    await waitFor(() => expect(termOf('pane_1').output).toContain('Agente listo'));
    const instance = termOf('pane_1');
    expect(screen.getByTestId('terminals-view-host').getAttribute('data-active')).toBe('true');

    // A otra vista: oculta, no desmontada. El slider es el de la vista activa, no el de terminales.
    rerender(workspace('kanban'));
    const host = screen.getByTestId('terminals-view-host');
    expect(host.getAttribute('data-active')).toBe('false');
    expect(host.style.display).toBe('none');
    expect(host.contains(screen.getByTestId('terminal-pane-pane_1'))).toBe(true);
    expect(instance.disposed).toBe(false);
    expect(screen.queryByTestId('terminals-slider-controls')).toBeNull();

    // Sigue sondeando (a ritmo lento) mientras esta oculta, sin volver a pedir desde el principio.
    const before = api.pty_poll_output.mock.calls.length;
    await waitFor(() => expect(api.pty_poll_output.mock.calls.length).toBeGreaterThan(before), { timeout: 3000 });
    api.pty_poll_output.mock.calls.slice(before).forEach(([, seq]) => expect(seq).toBeGreaterThan(0));

    // Y al volver es la MISMA instancia, con su contenido, sin duplicar la salida.
    rerender(workspace('terminals'));
    expect(screen.getByTestId('terminals-view-host').style.display).toBe('block');
    expect(await screen.findByTestId('terminals-slider-controls')).toBeTruthy();
    expect(Terminal.instances).toHaveLength(1);
    expect(termOf('pane_1')).toBe(instance);
    expect(instance.disposed).toBe(false);
    expect(instance.output.match(/Agente listo/g)).toHaveLength(1);
    // Al reactivar se vuelve a pedir el estado actual del enjambre (pty_get_matrix_status).
    await waitFor(() => expect(api.pty_get_matrix_status.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('la vista de terminales no se monta hasta que se abre por primera vez', async () => {
    const api = {
      ...makeMatrixApi(),
      get_kanban_board: vi.fn(async () => ({ success: true, cards: [] })),
      get_okf_tags_index: vi.fn(async () => ({ success: true, tags: {} })),
      subscribe_project_events: vi.fn(async () => ({ success: true })),
      poll_project_events: vi.fn(async () => ({ success: true, events: [] }))
    };
    render(
      <ProjectWorkspace project={PROJECT} activeTab="kanban" onTabChange={vi.fn()} onBack={vi.fn()} onNewTask={vi.fn()} api={api} />
    );
    expect(screen.queryByTestId('terminals-view-host')).toBeNull();
    expect(api.pty_get_matrix_status).not.toHaveBeenCalled();
  });
});

describe('SwarmTerminalMatrix: Auto-Relay, badges EGM-86 y Loop Breaker EGM-87', () => {
  it('renderiza badges mcpInjection (EGM-86) y activityState (idle / working) en TerminalPane', async () => {
    const api = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => ({
        success: true,
        degraded: false,
        hub_url: 'http://127.0.0.1:8765/mcp',
        hub_running: true,
        auto_relay_enabled: false,
        relay_status: { auto_relay_enabled: false, tripped_cases: [], deferred_count: 0 },
        panes: [
          { pane_id: 'pane_1', node_uuid: 'n1', status: 'active', cli_profile: 'claude', role: 'implementer', pid: 101 },
          { pane_id: 'pane_2', node_uuid: 'n2', status: 'active', cli_profile: 'codex', role: 'reviewer', pid: 102 }
        ],
        sessions: [
          {
            pane_id: 'pane_1',
            session_id: 's1',
            alive: true,
            paused: false,
            cli_profile: 'claude',
            role: 'implementer',
            cwd: '/tmp/p1',
            mcp_injection: { status: 'injected', verified: true, reason: 'config ok' },
            activity_state: 'idle',
            activity_source: 'osc_7501',
            idle: true
          },
          {
            pane_id: 'pane_2',
            session_id: 's2',
            alive: true,
            paused: false,
            cli_profile: 'codex',
            role: 'reviewer',
            cwd: '/tmp/p2',
            mcp_injection: { status: 'unsupported', verified: false, reason: 'manual' },
            activity_state: 'working',
            activity_source: 'osc_7501',
            idle: false
          }
        ]
      }))
    });

    renderMatrix(api);

    const mcp1 = await screen.findByTestId('pane-mcp-badge-pane_1');
    expect(mcp1.textContent).toBe('MCP Hub');
    expect(mcp1.getAttribute('title')).toContain('verified=true');
    const idleBadge = screen.getByTestId('pane-activity-badge-pane_1');
    expect(idleBadge.textContent).toBe('Sin actividad');
    expect(idleBadge.textContent).not.toMatch(/Libre/);
    // F0: la actividad es solo informativa y el tooltip lo dice (no autoriza escribir en la terminal).
    expect(idleBadge.getAttribute('title')).toContain('Solo informativo');

    const mcp2 = screen.getByTestId('pane-mcp-badge-pane_2');
    expect(mcp2.textContent).toBe('MCP manual');
    expect(screen.getByTestId('pane-activity-badge-pane_2').textContent).toBe('Trabajando');
  });

  it('el Auto-Relay es por terminal: pty_set_auto_relay(panel, true) cambia solo esa, sin parrafo largo y sin Gate 0c', async () => {
    const pty_set_auto_relay = vi.fn(async (paneId, enabled) => ({
      success: true,
      pane_id: paneId,
      auto_relay_enabled: Boolean(enabled)
    }));
    const api = makeMatrixApi({ pty_set_auto_relay });
    renderMatrix(api);
    await openCli('claude', 'pane_1');
    await openCli('codex', 'pane_2');

    const toggle1 = await screen.findByTestId('pane-auto-relay-toggle-pane_1');
    const toggle2 = screen.getByTestId('pane-auto-relay-toggle-pane_2');
    expect(toggle1.textContent).toContain('OFF');
    expect(toggle2.textContent).toContain('OFF');
    expect(screen.queryByTestId('auto-relay-section')).toBeNull();
    expect(screen.queryByTestId('auto-relay-description')).toBeNull();
    expect(screen.getByTestId('pane-auto-relay-hint-pane_1')).toBeTruthy();
    expect(screen.getByTestId('pane-auto-relay-hint-pane_2')).toBeTruthy();

    fireEvent.click(toggle2);
    await waitFor(() => expect(pty_set_auto_relay).toHaveBeenCalledWith('pane_2', true));
    await waitFor(() => expect(toggle2.textContent).toContain('ON'));
    expect(toggle2.getAttribute('aria-pressed')).toBe('true');
    expect(toggle1.textContent).toContain('OFF');
    expect(screen.queryByTestId('auto-relay-gate0c-note')).toBeNull();

    fireEvent.click(toggle2);
    await waitFor(() => expect(pty_set_auto_relay).toHaveBeenLastCalledWith('pane_2', false));
    await waitFor(() => expect(toggle2.textContent).toContain('OFF'));
  });

  it('el estado de Auto-Relay de cada terminal viene del backend (persistido) al recargar', async () => {
    const api = makeMatrixApi({
      pty_set_auto_relay: vi.fn(async () => ({ success: true })),
      pty_get_matrix_status: vi.fn(async () => ({
        success: true, degraded: false, hub_url: null, hub_running: false,
        panes: [
          { pane_id: 'pane_1', node_uuid: 'n1', status: 'active', cli_profile: 'claude', project_uuid: PROJECT.project_uuid, auto_relay_enabled: true },
          { pane_id: 'pane_2', node_uuid: 'n2', status: 'active', cli_profile: 'claude', project_uuid: PROJECT.project_uuid, auto_relay_enabled: false }
        ],
        sessions: [
          { pane_id: 'pane_1', session_id: 's1', alive: true, cli_profile: 'claude' },
          { pane_id: 'pane_2', session_id: 's2', alive: true, cli_profile: 'claude' }
        ]
      }))
    });
    renderMatrix(api);
    expect((await screen.findByTestId('pane-auto-relay-toggle-pane_1')).textContent).toContain('ON');
    expect(screen.getByTestId('pane-auto-relay-toggle-pane_2').textContent).toContain('OFF');
  });

  it('si el dialogo nativo rechaza activar el Auto-Relay, el interruptor de esa terminal sigue en OFF y se avisa', async () => {
    const pty_set_auto_relay = vi.fn(async () => ({
      success: false,
      code: 'CONFIRMATION_DECLINED',
      message: 'Operacion cancelada: no se confirmo en el dialogo del sistema.'
    }));
    const api = makeMatrixApi({ pty_set_auto_relay });
    renderMatrix(api);
    await openCli('claude', 'pane_1');

    const toggle = await screen.findByTestId('pane-auto-relay-toggle-pane_1');
    fireEvent.click(toggle);
    await waitFor(() => expect(pty_set_auto_relay).toHaveBeenCalledWith('pane_1', true));
    await waitFor(() => expect(toggle.getAttribute('aria-pressed')).toBe('false'));
    expect(toggle.textContent).toContain('OFF');
    expect(toggle.disabled).toBe(false);
  });

  it('muestra banner y boton reset-loop-breaker-btn (EGM-87) cuando relay_status.tripped_cases no esta vacio', async () => {
    let tripped = ['card:card_99'];
    const reset_loop_breaker = vi.fn(async () => {
      tripped = [];
      return { success: true, code: 'OK' };
    });
    const api = makeMatrixApi({
      reset_loop_breaker,
      pty_get_matrix_status: vi.fn(async () => ({
        success: true,
        degraded: false,
        hub_url: 'http://127.0.0.1:8765/mcp',
        hub_running: true,
        auto_relay_enabled: true,
        relay_status: {
          auto_relay_enabled: true,
          tripped_cases: tripped,
          deferred_count: 0
        },
        panes: [],
        sessions: []
      }))
    });

    renderMatrix(api);

    const banner = await screen.findByTestId('loop-breaker-banner');
    expect(banner.textContent).toContain('card:card_99');

    const resetBtn = screen.getByTestId('reset-loop-breaker-btn');
    fireEvent.click(resetBtn);

    await waitFor(() => expect(reset_loop_breaker).toHaveBeenCalledWith(PROJECT.project_uuid, 'card_99', ''));
    await waitFor(() => expect(screen.queryByTestId('loop-breaker-banner')).toBeNull());
  });
});

describe('SwarmTerminalMatrix: registro de agy opt-in y badge verificado (R1.4/R1.5)', () => {
  const statusWith = (mcpInjection) => ({
    success: true,
    degraded: false,
    hub_url: 'http://127.0.0.1:8765/mcp',
    hub_running: true,
    auto_relay_enabled: false,
    relay_status: { auto_relay_enabled: false, tripped_cases: [], deferred_count: 0 },
    panes: [{ pane_id: 'pane_1', node_uuid: 'n1', status: 'active', cli_profile: 'antigravity', role: 'implementer', pid: 101 }],
    sessions: [
      {
        pane_id: 'pane_1',
        session_id: 's1',
        alive: true,
        paused: false,
        cli_profile: 'antigravity',
        role: 'implementer',
        cwd: '/tmp/p1',
        mcp_injection: mcpInjection
      }
    ]
  });

  it('needs_consent muestra «MCP pendiente» y el boton registra con el dialogo nativo del puente', async () => {
    const pty_register_agy_mcp = vi.fn(async () => ({ success: true, code: 'OK', message: 'MCP2P registrado en Antigravity.' }));
    const api = makeMatrixApi({
      pty_register_agy_mcp,
      pty_get_matrix_status: vi.fn(async () =>
        statusWith({ status: 'needs_consent', method: 'stdio_env_proxy', verified: true, reason: 'needs_consent' })
      )
    });
    renderMatrix(api);

    const badge = await screen.findByTestId('pane-mcp-badge-pane_1');
    expect(badge.textContent).toBe('MCP pendiente');
    const button = screen.getByTestId('pane-mcp-register-pane_1');
    expect(button.textContent).toBe('Registrar MCP2P en Antigravity');
    fireEvent.click(button);
    await waitFor(() => expect(pty_register_agy_mcp).toHaveBeenCalledTimes(1));
  });

  it('un rechazo del dialogo se muestra como aviso y no rompe el panel', async () => {
    const pty_register_agy_mcp = vi.fn(async () => ({
      success: false,
      code: 'CONFIRMATION_DECLINED',
      message: 'La accion fue rechazada en el dialogo de confirmacion.'
    }));
    const api = makeMatrixApi({
      pty_register_agy_mcp,
      pty_get_matrix_status: vi.fn(async () => statusWith({ status: 'needs_consent', verified: true }))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('pane-mcp-register-pane_1'));
    await waitFor(() => expect(pty_register_agy_mcp).toHaveBeenCalled());
    expect(screen.getByTestId('terminal-pane-pane_1')).toBeTruthy();
  });

  it('sin el metodo del puente no hay boton de registro', async () => {
    const api = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () => statusWith({ status: 'needs_consent', verified: true }))
    });
    delete api.pty_register_agy_mcp;
    renderMatrix(api);
    await screen.findByTestId('pane-mcp-badge-pane_1');
    expect(screen.queryByTestId('pane-mcp-register-pane_1')).toBeNull();
  });

  it('injected con verified=false (binario empaquetado) NO se anuncia como «MCP Hub»', async () => {
    const api = makeMatrixApi({
      pty_get_matrix_status: vi.fn(async () =>
        statusWith({ status: 'injected', method: 'stdio_env_proxy', verified: false, reason: 'stdio_hub_proxy' })
      )
    });
    renderMatrix(api);
    const badge = await screen.findByTestId('pane-mcp-badge-pane_1');
    expect(badge.textContent).toBe('MCP sin verificar');
    expect(badge.textContent).not.toMatch(/MCP Hub/);
    expect(badge.getAttribute('title')).toContain('verified=false');
  });

  it('panel antigravity con status injected muestra «Quitar MCP2P de Antigravity», lo deshabilita mientras corre e invoca pty_unregister_agy_mcp; panel claude NO lo muestra', async () => {
    let resolveUnregister;
    const pty_unregister_agy_mcp = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveUnregister = resolve;
        })
    );
    const pty_get_matrix_status = vi.fn(async () => ({
      success: true,
      degraded: false,
      hub_url: 'http://127.0.0.1:8765/mcp',
      hub_running: true,
      auto_relay_enabled: false,
      relay_status: { auto_relay_enabled: false, tripped_cases: [], deferred_count: 0 },
      panes: [
        { pane_id: 'pane_1', node_uuid: 'n1', status: 'active', cli_profile: 'antigravity', role: 'implementer', pid: 101 },
        { pane_id: 'pane_2', node_uuid: 'n2', status: 'active', cli_profile: 'claude', role: 'implementer', pid: 102 }
      ],
      sessions: [
        {
          pane_id: 'pane_1',
          session_id: 's1',
          alive: true,
          paused: false,
          cli_profile: 'antigravity',
          role: 'implementer',
          cwd: '/tmp/p1',
          mcp_injection: { status: 'injected', method: 'stdio_hub_proxy', verified: true }
        },
        {
          pane_id: 'pane_2',
          session_id: 's2',
          alive: true,
          paused: false,
          cli_profile: 'claude',
          role: 'implementer',
          cwd: '/tmp/p2',
          mcp_injection: { status: 'injected', method: 'stdio_hub_proxy', verified: true }
        }
      ]
    }));

    const api = makeMatrixApi({ pty_unregister_agy_mcp, pty_get_matrix_status });
    renderMatrix(api);

    const unregisterBtn = await screen.findByTestId('pane-mcp-unregister-pane_1');
    expect(unregisterBtn.textContent).toBe('Quitar MCP2P de Antigravity');
    expect(unregisterBtn.disabled).toBe(false);
    expect(screen.queryByTestId('pane-mcp-unregister-pane_2')).toBeNull();

    fireEvent.click(unregisterBtn);
    await waitFor(() => expect(unregisterBtn.disabled).toBe(true));
    fireEvent.click(unregisterBtn);
    expect(pty_unregister_agy_mcp).toHaveBeenCalledTimes(1);

    resolveUnregister({
      success: true,
      code: 'OK',
      message: 'Se quitó el registro de MCP2P en Antigravity.'
    });
    await waitFor(() => expect(unregisterBtn.disabled).toBe(false));
    await waitFor(() => expect(pty_get_matrix_status).toHaveBeenCalledTimes(2));
  });

  it('EGM-85: muestra native-dialog-pending-banner solo en acciones nativas y usa AppConfirmDialog en Auto-Relay e instalación', async () => {
    let resolveRegister;
    const pty_register_agy_mcp = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveRegister = resolve;
        })
    );
    const pty_set_auto_relay = vi.fn(async (_paneId, enabled) => {
      if (enabled) {
        return {
          success: false,
          code: 'CONFIRMATION_REQUIRED',
          confirmation: {
            token: 'tok-auto-1',
            action_id: 'auto_relay.enable',
            title: 'Activar Auto-Relay',
            message: 'Confirmar Auto-Relay',
            rememberable: true,
            expires_in_s: 120
          }
        };
      }
      return { success: true, auto_relay_enabled: false };
    });
    let resolveSetPath;
    const pty_set_cli_path = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveSetPath = resolve;
        })
    );
    const pty_prepare_install = vi.fn(async (cliId) => ({
      success: true,
      requires_confirmation: true,
      confirmation_token: 'tok_123',
      display_name: cliId,
      command: 'npm install -g @example/missing_cli',
      method: 'npm',
      downloads_script: false
    }));
    const pty_install_cli = vi.fn(async () => ({
      success: false,
      code: 'CONFIRMATION_REQUIRED',
      confirmation: {
        token: 'tok-inst-1',
        action_id: 'cli.install.package_manager',
        title: 'Instalar missing_cli',
        message: 'npm install -g @example/missing_cli',
        rememberable: true,
        expires_in_s: 120
      }
    }));
    const ui_confirm_action = vi.fn(async (token) => {
      if (token === 'tok-auto-1') {
        return { success: true, pane_id: 'pane_1', auto_relay_enabled: true };
      }
      return { success: true, code: 'INSTALL_STARTED', pane_id: 'pane_3', session_id: 's3', pid: 303 };
    });

    const api = makeMatrixApi({
      pty_register_agy_mcp,
      pty_set_auto_relay,
      pty_set_cli_path,
      pty_prepare_install,
      pty_install_cli,
      ui_confirm_action,
      pty_list_cli_catalog: vi.fn(async () =>
        makeCatalogResponse([makeCli('claude'), notInstalled('missing_cli')])
      ),
      pty_get_matrix_status: vi.fn(async () =>
        statusWith({ status: 'needs_consent', method: 'stdio_env_proxy', verified: true, reason: 'needs_consent' })
      )
    });
    renderMatrix(api);

    expect(screen.queryByTestId('native-dialog-pending-banner')).toBeNull();

    fireEvent.click(await screen.findByTestId('pane-mcp-register-pane_1'));
    const banner = await screen.findByTestId('native-dialog-pending-banner');
    expect(banner.getAttribute('role')).toBe('status');
    expect(banner.textContent).toContain('Confirme la acción en el diálogo del sistema');

    resolveRegister({ success: true, code: 'OK', message: 'MCP2P registrado en Antigravity.' });
    await waitFor(() => expect(screen.queryByTestId('native-dialog-pending-banner')).toBeNull());

    // Activar Auto-Relay usa AppConfirmDialog (sin native-dialog-pending-banner); apagarlo no pregunta.
    const autoRelayToggle = await screen.findByTestId('pane-auto-relay-toggle-pane_1');
    fireEvent.click(autoRelayToggle);
    expect(await screen.findByRole('alertdialog')).toBeTruthy();
    expect(screen.queryByTestId('native-dialog-pending-banner')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('app-confirm-accept').disabled).toBe(false), { timeout: 1500 });
    fireEvent.click(screen.getByTestId('app-confirm-accept'));
    await waitFor(() => expect(ui_confirm_action).toHaveBeenCalledWith('tok-auto-1', true, false));
    await waitFor(() => expect(autoRelayToggle.textContent).toContain('ON'));

    fireEvent.click(autoRelayToggle);
    await waitFor(() => expect(pty_set_auto_relay).toHaveBeenLastCalledWith('pane_1', false));
    expect(screen.queryByTestId('native-dialog-pending-banner')).toBeNull();

    // handleSetCliPath sigue abriendo dialogo nativo y muestra el banner.
    fireEvent.click(await screen.findByTestId('cli-path-toggle-claude'));
    fireEvent.change(await screen.findByTestId('cli-path-input-claude'), { target: { value: '/usr/local/bin/claude' } });
    fireEvent.click(screen.getByTestId('cli-path-save-claude'));
    expect((await screen.findByTestId('native-dialog-pending-banner')).textContent).toContain(
      'Confirme la acción en el diálogo del sistema'
    );
    resolveSetPath({ success: true, code: 'OK', cli_id: 'claude', configured_path: '/usr/local/bin/claude' });
    await waitFor(() => expect(screen.queryByTestId('native-dialog-pending-banner')).toBeNull());

    // handleConfirmInstall usa AppConfirmDialog (sin native-dialog-pending-banner) y resuelve con ui_confirm_action.
    fireEvent.click(await screen.findByTestId('cli-install-missing_cli'));
    fireEvent.click(await screen.findByTestId('install-confirm-btn'));
    expect(await screen.findByRole('alertdialog')).toBeTruthy();
    expect(screen.queryByTestId('native-dialog-pending-banner')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('app-confirm-accept').disabled).toBe(false), { timeout: 1500 });
    fireEvent.click(screen.getByTestId('app-confirm-accept'));
    await waitFor(() => expect(ui_confirm_action).toHaveBeenCalledWith('tok-inst-1', true, false));
    expect(screen.queryByTestId('native-dialog-pending-banner')).toBeNull();
  });
});
