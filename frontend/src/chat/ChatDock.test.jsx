// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import { ChatDock } from './index';
import { lastSeenKey } from './useChatFeed';

const ENTRIES = [
  { id: 'a1', ts_ms: 100, source: 'agent', sender: { principal: 'agent:orq', label: 'Orquestador', kind: 'agent', role_label: 'orquestador', color: 'c1' }, recipients: [], text: 'primero', kind: 'message', tick: 'delivered', tick_label: 'Entregado', origin: 'local' },
  { id: 'a2', ts_ms: 200, source: 'agent', sender: { principal: 'agent:claude', label: 'Claude', kind: 'agent', role_label: 'trabajador', color: 'c2' }, recipients: [], text: 'segundo', kind: 'message', tick: 'read', tick_label: 'Leído', origin: 'local' },
  { id: 'h1', ts_ms: 250, source: 'human', sender: { principal: 'human:me', label: 'Ana', kind: 'human', role_label: '', color: 'c3' }, recipients: [], text: 'mio', kind: 'message', tick: 'queued', tick_label: 'En cola', origin: 'local' }
];

const CHANNELS = [
  { kind: 'agent', label: 'Agentes locales', available: true, reason: null },
  { kind: 'node_devs', label: 'Nodos remotos', available: false, reason: 'Sin nodos remotos conectados' }
];

const PARTICIPANTS = [
  { principal: 'agent:orq', label: 'Orquestador', mention: '@orquestador', kind: 'agent', role: 'orchestrator', role_label: 'orquestador' },
  { principal: 'agent:claude', label: 'Claude', mention: '@claude', kind: 'agent', role: 'worker', role_label: 'trabajador' }
];

function makeApi(overrides = {}) {
  return {
    chat_get_feed: vi.fn(async () => ({ success: true, entries: ENTRIES, cursor_ms: 250, sources: [] })),
    chat_get_channels: vi.fn(async () => ({ success: true, channels: CHANNELS, participants: PARTICIPANTS })),
    chat_send: vi.fn(async () => ({ success: true, results: [] })),
    ...overrides
  };
}

function setWidth(px) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: px });
  window.dispatchEvent(new Event('resize'));
}

const dialog = () => screen.queryByRole('dialog', { name: 'Chat' });
const floatingButton = () => screen.getByRole('button', { name: /chat:/i });

beforeEach(() => {
  window.localStorage.clear();
  setWidth(1280);
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('ChatDock: montaje', () => {
  it('sin chat_get_feed en el puente no pinta nada', () => {
    const { container } = render(<ChatDock api={{}} projectUuid="p1" />);
    expect(container.innerHTML).toBe('');
  });

  it('sin proyecto activo no pinta nada', () => {
    const { container } = render(<ChatDock api={makeApi()} projectUuid={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('el boton flotante anuncia los sin leer (los del humano no cuentan)', async () => {
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    const button = await screen.findByRole('button', { name: 'Abrir chat: 2 sin leer' });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(button.getAttribute('aria-controls')).toBeTruthy();
    expect(screen.getByTestId('chat-dock-badge').textContent).toBe('2');
  });

  it('la insignia suma el badge de las pestañas extra', async () => {
    const extraTabs = [{ id: 'permisos', label: 'Permisos', badge: 3, render: () => <div>contenido permisos</div> }];
    render(<ChatDock api={makeApi()} projectUuid="p1" extraTabs={extraTabs} />);
    await screen.findByRole('button', { name: 'Abrir chat: 2 sin leer, 3 pendientes en otras pestañas' });
    expect(screen.getByTestId('chat-dock-badge').textContent).toBe('5');
  });
});

describe('ChatDock: panel', () => {
  it('abre con el boton, enlaza aria-controls con el dialogo y guarda el estado', async () => {
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    const button = await screen.findByRole('button', { name: /Abrir chat/ });
    fireEvent.click(button);
    const panel = dialog();
    expect(panel).toBeTruthy();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(button.getAttribute('aria-controls')).toBe(panel.id);
    expect(window.localStorage.getItem('mcp2p.chat.open')).toBe('1');
  });

  it('recuerda que estaba abierto al montar', () => {
    window.localStorage.setItem('mcp2p.chat.open', '1');
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    expect(dialog()).toBeTruthy();
  });

  it('Esc cierra el panel y devuelve el foco al boton', async () => {
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    const panel = dialog();
    expect(document.activeElement).toBe(panel);
    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(floatingButton());
    expect(window.localStorage.getItem('mcp2p.chat.open')).toBe('0');
  });

  it('el boton Cerrar chat cierra el panel', async () => {
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar chat' }));
    expect(dialog()).toBeNull();
  });

  it('con el panel abierto en Conversacion lo recibido se marca como leido', async () => {
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    await screen.findByRole('button', { name: 'Abrir chat: 2 sin leer' });
    fireEvent.click(screen.getByRole('button', { name: /Abrir chat/ }));
    await waitFor(() => expect(screen.queryByTestId('chat-dock-badge')).toBeNull());
    expect(window.localStorage.getItem(lastSeenKey('p1'))).toBe('250');
  });

  it('Esc dentro de la lista de menciones cierra solo la lista', async () => {
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    const textarea = screen.getByRole('combobox', { name: 'Mensaje' });
    fireEvent.change(textarea, { target: { value: '@' } });
    expect(screen.getByRole('listbox')).toBeTruthy();
    fireEvent.keyDown(textarea, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(dialog()).toBeTruthy();
  });

  it('enviar desde el redactor llama a chat_send y vuelve a leer el feed', async () => {
    const api = makeApi();
    render(<ChatDock api={api} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    const textarea = screen.getByRole('combobox', { name: 'Mensaje' });
    const reads = api.chat_get_feed.mock.calls.length;
    fireEvent.change(textarea, { target: { value: 'hola' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    await waitFor(() => expect(api.chat_send).toHaveBeenCalledWith('p1', 'hola'));
    await waitFor(() => expect(api.chat_get_feed.mock.calls.length).toBeGreaterThan(reads));
  });

  it('un canal no disponible va marcado como deshabilitado y con su motivo visible', async () => {
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    const off = await screen.findByTestId('chat-channel-node_devs');
    expect(off.getAttribute('aria-disabled')).toBe('true');
    expect(off.getAttribute('title')).toBe('Sin nodos remotos conectados');
    expect(screen.getByText('Nodos remotos: Sin nodos remotos conectados')).toBeTruthy();
    expect(screen.getByTestId('chat-channel-agent').getAttribute('aria-disabled')).toBeNull();
  });

  it('las pestañas extra son tabs y muestran su propio contenido', async () => {
    const extraTabs = [{ id: 'permisos', label: 'Permisos', badge: 1, render: () => <div>contenido permisos</div> }];
    render(<ChatDock api={makeApi()} projectUuid="p1" extraTabs={extraTabs} />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    expect(screen.getAllByRole('tab')).toHaveLength(2);
    fireEvent.click(screen.getByRole('tab', { name: /Permisos/ }));
    expect(screen.getByRole('tab', { name: /Permisos/ }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByText('contenido permisos')).toBeTruthy();
    expect(screen.queryByRole('log')).toBeNull();
  });

  it('un error del feed muestra Reintentar y lo vuelve a intentar', async () => {
    const api = makeApi({
      chat_get_feed: vi.fn()
        .mockRejectedValueOnce(new Error('puente caido'))
        .mockResolvedValue({ success: true, entries: ENTRIES, cursor_ms: 250, sources: [] })
    });
    render(<ChatDock api={api} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    const retry = await screen.findByRole('button', { name: 'Reintentar' });
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText('segundo')).toBeTruthy());
    expect(screen.queryByRole('button', { name: 'Reintentar' })).toBeNull();
  });
});

describe('ChatDock: diseño responsive', () => {
  it('a 375 px el panel ocupa toda la pantalla sin ancho fijo que desborde', async () => {
    setWidth(375);
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    const panel = dialog();
    expect(panel.style.width).toBe('100%');
    expect(parseFloat(panel.style.left)).toBe(0);
    for (const el of [panel, ...panel.querySelectorAll('*')]) {
      const px = /^(\d+(?:\.\d+)?)px$/.exec((el.style?.width || '').trim());
      if (px) expect(Number(px[1])).toBeLessThanOrEqual(343);
    }
  });

  it('en escritorio es un panel de 380 px entre la barra superior y el pie', async () => {
    setWidth(1280);
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    const panel = dialog();
    expect(panel.style.width).toBe('380px');
    expect(panel.style.top).toBe('50px');
    expect(panel.style.bottom).toBe('28px');
    expect(parseFloat(panel.style.right)).toBe(0);
  });

  it('en escritorio, con el panel abierto, el boton flotante queda a la izquierda del panel', async () => {
    setWidth(1280);
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    expect(floatingButton().style.right).toBe('396px');
  });

  it('cambia de disposicion al redimensionar la ventana', async () => {
    setWidth(1280);
    render(<ChatDock api={makeApi()} projectUuid="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Abrir chat/ }));
    expect(dialog().style.width).toBe('380px');
    act(() => setWidth(500));
    expect(dialog().style.width).toBe('100%');
  });
});
