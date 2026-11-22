// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { ChatFeed } from './ChatFeed';

afterEach(cleanup);

const entry = (id, ts, principal = 'agent:orq', extra = {}) => ({
  id,
  ts_ms: ts,
  source: 'agent',
  sender: { principal, label: principal === 'agent:orq' ? 'Orquestador' : 'Claude', kind: 'agent', role_label: '', color: 'c1' },
  recipients: [],
  text: `texto ${id}`,
  kind: 'message',
  tick: 'delivered',
  tick_label: 'Entregado',
  origin: 'local',
  ...extra
});

describe('ChatFeed', () => {
  it('es una region log en vivo', () => {
    render(<ChatFeed entries={[entry('1', 1)]} />);
    const log = screen.getByRole('log');
    expect(log.getAttribute('aria-live')).toBe('polite');
  });

  it('sin mensajes muestra el estado vacio en espanol', () => {
    render(<ChatFeed entries={[]} />);
    expect(screen.getByText('Todavía no hay mensajes entre agentes en este proyecto')).toBeTruthy();
  });

  it('mientras carga y sin mensajes muestra "Cargando" en vez del estado vacio', () => {
    render(<ChatFeed entries={[]} loading />);
    expect(screen.queryByText(/Todavía no hay mensajes/)).toBeNull();
    expect(screen.getByText('Cargando mensajes…')).toBeTruthy();
  });

  it('pinta cada mensaje y solo cabecera en el primero de cada emisor consecutivo', () => {
    render(<ChatFeed entries={[entry('1', 1), entry('2', 2), entry('3', 3, 'agent:claude')]} />);
    const bubbles = screen.getAllByTestId('chat-bubble');
    expect(bubbles).toHaveLength(3);
    expect(bubbles.map((b) => b.getAttribute('data-continuation'))).toEqual(['false', 'true', 'false']);
    expect(screen.getAllByText('Orquestador')).toHaveLength(1);
    expect(screen.getByText('Claude')).toBeTruthy();
  });

  it('un error se muestra con texto y el boton Reintentar llama a onRetry', () => {
    const onRetry = vi.fn();
    render(<ChatFeed entries={[]} error="sin conexion" onRetry={onRetry} />);
    expect(screen.getByRole('alert').textContent).toContain('sin conexion');
    expect(screen.queryByText(/Todavía no hay mensajes/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('sin onRetry el error no ofrece boton', () => {
    render(<ChatFeed entries={[]} error="fallo" />);
    expect(screen.queryByRole('button', { name: 'Reintentar' })).toBeNull();
  });

  it('muestra una linea discreta por cada fuente con ok:false y su motivo', () => {
    const sources = [
      { id: 'agents', ok: true, reason: null },
      { id: 'node-b', ok: false, reason: 'sin respuesta en 5 s' }
    ];
    render(<ChatFeed entries={[entry('1', 1)]} sources={sources} />);
    const issues = screen.getAllByTestId('chat-source-issue');
    expect(issues).toHaveLength(1);
    expect(issues[0].textContent).toContain('node-b');
    expect(issues[0].textContent).toContain('sin respuesta en 5 s');
  });

  it('desplaza al final al llegar mensajes si el usuario ya estaba abajo', () => {
    const { rerender } = render(<ChatFeed entries={[entry('1', 1)]} />);
    const log = screen.getByRole('log');
    Object.defineProperty(log, 'scrollHeight', { configurable: true, value: 1000 });
    Object.defineProperty(log, 'clientHeight', { configurable: true, value: 200 });
    rerender(<ChatFeed entries={[entry('1', 1), entry('2', 2)]} />);
    expect(log.scrollTop).toBe(1000);
  });

  it('no mueve la vista si el usuario ha subido a leer', () => {
    const { rerender } = render(<ChatFeed entries={[entry('1', 1)]} />);
    const log = screen.getByRole('log');
    Object.defineProperty(log, 'scrollHeight', { configurable: true, writable: true, value: 1000 });
    Object.defineProperty(log, 'clientHeight', { configurable: true, value: 200 });
    log.scrollTop = 100;
    fireEvent.scroll(log);
    log.scrollTop = 100;
    rerender(<ChatFeed entries={[entry('1', 1), entry('2', 2)]} />);
    expect(log.scrollTop).toBe(100);
  });

  it('el texto de los mensajes no se interpreta como HTML', () => {
    const { container } = render(<ChatFeed entries={[entry('x', 1, 'agent:orq', { text: '<b>negrita</b>' })]} />);
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getByText('<b>negrita</b>')).toBeTruthy();
  });
});
