// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import { SendToAgent } from './SendToAgent';

afterEach(cleanup);

const TARGETS = [
  { paneId: 'pane_1', label: 'Terminal 1 · Claude' },
  { paneId: 'pane_2', label: 'Terminal 2 · Gemini' }
];

function type(text) {
  fireEvent.change(screen.getByTestId('send-to-agent-text'), { target: { value: text } });
}

describe('SendToAgent', () => {
  it('sin terminales abiertas: explica que hacer y no muestra el formulario', () => {
    render(<SendToAgent targets={[]} onSend={vi.fn()} />);
    expect(screen.getByTestId('send-to-agent-empty').textContent).toContain('Abra un agente');
    expect(screen.queryByTestId('send-to-agent-submit')).toBeNull();
  });

  it('explica que llega por la bandeja y que no se escribe en la terminal', () => {
    render(<SendToAgent targets={TARGETS} onSend={vi.fn()} />);
    const section = screen.getByTestId('send-to-agent');
    expect(section.textContent).toContain('No se escribe nada en su');
    expect(section.textContent).toContain('inbox_next');
  });

  it('no deja enviar vacio; con texto envia (panel, texto, tipo) y limpia el campo al acertar', async () => {
    const onSend = vi.fn(async () => ({ ok: true, text: 'Enviado a la bandeja de Terminal 2 · Gemini.' }));
    render(<SendToAgent targets={TARGETS} onSend={onSend} />);
    expect(screen.getByTestId('send-to-agent-submit').disabled).toBe(true);
    fireEvent.change(screen.getByTestId('send-to-agent-target'), { target: { value: 'pane_2' } });
    fireEvent.change(screen.getByTestId('send-to-agent-kind'), { target: { value: 'task.request' } });
    type('  revisa develop  ');
    expect(screen.getByTestId('send-to-agent-submit').disabled).toBe(false);
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('pane_2', '  revisa develop  ', 'task.request'));
    await waitFor(() => expect(screen.getByTestId('send-to-agent-result').textContent).toContain('Enviado a la bandeja'));
    expect(screen.getByTestId('send-to-agent-text').value).toBe('');
  });

  it('si falla muestra la causa y conserva el texto para reintentar', async () => {
    const onSend = vi.fn(async () => ({ ok: false, text: 'No se pudo enviar el mensaje: La terminal de destino ya no está abierta. Ábrala de nuevo o elija otra.' }));
    render(<SendToAgent targets={TARGETS} onSend={onSend} />);
    type('hola');
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() => expect(screen.getByTestId('send-to-agent-result').getAttribute('role')).toBe('alert'));
    expect(screen.getByTestId('send-to-agent-result').textContent).toContain('ya no está abierta');
    expect(screen.getByTestId('send-to-agent-text').value).toBe('hola');
  });

  it('una excepcion del puente tambien se muestra con causa y accion', async () => {
    render(<SendToAgent targets={TARGETS} onSend={vi.fn(async () => { throw new Error('puente caido'); })} />);
    type('hola');
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() => expect(screen.getByTestId('send-to-agent-result').textContent).toContain('puente caido'));
    expect(screen.getByTestId('send-to-agent-result').textContent).toContain('Vuelva a intentarlo');
  });

  it('cargando: mientras envia el boton dice «Enviando…» y no se puede pulsar otra vez', async () => {
    let finish;
    const onSend = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    render(<SendToAgent targets={TARGETS} onSend={onSend} />);
    type('hola');
    fireEvent.click(screen.getByTestId('send-to-agent-submit'));
    await waitFor(() => expect(screen.getByTestId('send-to-agent-submit').textContent).toContain('Enviando'));
    expect(screen.getByTestId('send-to-agent-submit').disabled).toBe(true);
    finish({ ok: true, text: 'ok' });
    await waitFor(() => expect(screen.getByTestId('send-to-agent-submit').textContent).toContain('Enviar'));
  });

  it('avisa si el texto pasa de 8 KiB (en bytes, no en caracteres) y bloquea el envio', () => {
    render(<SendToAgent targets={TARGETS} onSend={vi.fn()} />);
    type('ñ'.repeat(4200)); // 8400 bytes
    expect(screen.getByTestId('send-to-agent-size').textContent).toContain('demasiado largo');
    expect(screen.getByTestId('send-to-agent-submit').disabled).toBe(true);
  });
});
