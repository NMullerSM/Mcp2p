// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { ChatBubble, formatTime } from './ChatBubble';
import { BUBBLE_COLORS } from './chatModel';

afterEach(cleanup);

const local = (h, m) => new Date(2026, 10, 21, h, m).getTime();

const AGENT_ENTRY = {
  id: 'e1',
  ts_ms: local(9, 5),
  source: 'agent',
  sender: { principal: 'agent:orq', label: 'Orquestador', kind: 'agent', role: 'orchestrator', role_label: 'orquestador', color: 'c3' },
  recipients: [{ principal: 'agent:claude', label: 'Claude' }],
  text: 'Revisa el parche',
  kind: 'message',
  thread_id: null,
  parent_id: null,
  hop: 1,
  tick: 'delivered',
  tick_label: 'Entregado',
  origin: 'local'
};

describe('ChatBubble', () => {
  it('muestra cabecera con emisor, rol, destinatario y hora HH:MM', () => {
    render(<ChatBubble entry={AGENT_ENTRY} />);
    expect(screen.getByText('Orquestador')).toBeTruthy();
    expect(screen.getByText('(orquestador)')).toBeTruthy();
    expect(screen.getByText('→ Claude')).toBeTruthy();
    expect(screen.getByText('09:05')).toBeTruthy();
  });

  it('una continuacion del mismo emisor no repite la cabecera', () => {
    const { container } = render(<ChatBubble entry={AGENT_ENTRY} continuation />);
    expect(screen.queryByText('Orquestador')).toBeNull();
    expect(container.querySelector('[data-continuation="true"]')).toBeTruthy();
    expect(screen.getByText('Revisa el parche')).toBeTruthy();
  });

  it('el texto es un nodo de texto: el HTML no se interpreta', () => {
    const payload = '<img src=x onerror="window.__pwned=1">';
    const { container } = render(<ChatBubble entry={{ ...AGENT_ENTRY, text: payload }} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(payload)).toBeTruthy();
    expect(window.__pwned).toBeUndefined();
  });

  it('el texto conserva los saltos de linea (pre-wrap)', () => {
    render(<ChatBubble entry={{ ...AGENT_ENTRY, text: 'linea 1\nlinea 2' }} />);
    const body = screen.getByText(/linea 1/);
    expect(body.style.whiteSpace).toBe('pre-wrap');
  });

  it('el estado de entrega se ve como simbolo y etiqueta, y tambien tiene aria-label y title', () => {
    render(<ChatBubble entry={AGENT_ENTRY} />);
    const tick = screen.getByLabelText('Entregado');
    expect(tick.textContent).toContain('✓');
    expect(tick.textContent).toContain('Entregado');
    expect(tick.getAttribute('title')).toBe('Entregado');
  });

  it('un tick con simbolo de advertencia lleva el tono de aviso', () => {
    render(<ChatBubble entry={{ ...AGENT_ENTRY, tick: 'doubtful', tick_label: 'Dudoso' }} />);
    const tick = screen.getByLabelText('Dudoso');
    expect(tick.textContent).toContain('⚠');
    expect(tick.getAttribute('data-tone')).toBe('warn');
  });

  it('el color del emisor viene de BUBBLE_COLORS', () => {
    render(<ChatBubble entry={AGENT_ENTRY} />);
    const body = screen.getByText('Revisa el parche');
    // jsdom normaliza hsl() a rgb(): se compara con el mismo valor pasado por un elemento de sonda.
    const probe = document.createElement('div');
    probe.style.borderColor = BUBBLE_COLORS.c3.border;
    expect(body.style.borderColor).toBe(probe.style.borderColor);
    expect(body.style.borderColor).not.toBe('');
  });

  it('los mensajes del humano van a la derecha y usan el color de marca', () => {
    const human = {
      ...AGENT_ENTRY,
      id: 'h1',
      sender: { principal: 'human:me', label: 'Ana', kind: 'human', role: '', role_label: '', color: 'c1' },
      recipients: [],
      tick: 'queued',
      tick_label: 'En cola'
    };
    const { container } = render(<ChatBubble entry={human} />);
    const bubble = container.querySelector('[data-testid="chat-bubble"]');
    expect(bubble.getAttribute('data-side')).toBe('right');
    expect(bubble.style.justifyContent).toBe('flex-end');
    expect(screen.getByLabelText('En cola')).toBeTruthy();
  });

  it('marca el origen cuando no es local', () => {
    render(<ChatBubble entry={{ ...AGENT_ENTRY, origin: 'nodo-remoto-2' }} />);
    expect(screen.getByText('Desde nodo-remoto-2')).toBeTruthy();
  });

  it('no marca origen local', () => {
    render(<ChatBubble entry={AGENT_ENTRY} />);
    expect(screen.queryByText(/^Desde /)).toBeNull();
  });

  it('una hora invalida no rompe la burbuja', () => {
    render(<ChatBubble entry={{ ...AGENT_ENTRY, ts_ms: 'nope' }} />);
    expect(screen.getByText('Revisa el parche')).toBeTruthy();
  });
});

describe('formatTime', () => {
  it('formatea HH:MM con cero a la izquierda', () => {
    expect(formatTime(local(7, 3))).toBe('07:03');
    expect(formatTime(NaN)).toBe('');
  });
});
