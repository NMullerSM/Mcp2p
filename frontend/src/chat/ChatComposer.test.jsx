// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { ChatComposer } from './ChatComposer';

afterEach(cleanup);

const PARTICIPANTS = [
  { principal: 'agent:orq', label: 'Orquestador', mention: '@orquestador', kind: 'agent', role: 'orchestrator', role_label: 'orquestador' },
  { principal: 'agent:claude', label: 'Claude', mention: '@claude', kind: 'agent', role: 'worker', role_label: 'trabajador' }
];

function setup(api = {}, props = {}) {
  const fullApi = { chat_send: vi.fn(async () => ({ success: true, results: [] })), ...api };
  const onSent = vi.fn();
  const utils = render(
    <ChatComposer api={fullApi} projectUuid="p1" participants={PARTICIPANTS} onSent={onSent} {...props} />
  );
  const textarea = screen.getByRole('combobox', { name: 'Mensaje' });
  return { ...utils, api: fullApi, onSent, textarea };
}

function type(textarea, value) {
  fireEvent.change(textarea, { target: { value } });
}

describe('ChatComposer: envio', () => {
  it('el campo tiene etiqueta accesible', () => {
    const { textarea } = setup();
    expect(textarea.tagName).toBe('TEXTAREA');
  });

  it('Enter envia el texto recortado y Shift+Enter no envia', async () => {
    const { api, textarea } = setup();
    type(textarea, '  hola equipo  ');
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(api.chat_send).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: 'Enter' });
    await waitFor(() => expect(api.chat_send).toHaveBeenCalledWith('p1', 'hola equipo'));
  });

  it('no envia texto vacio y el boton queda deshabilitado', () => {
    const { api, textarea } = setup();
    type(textarea, '   ');
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(api.chat_send).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Enviar' }).disabled).toBe(true);
  });

  it('tras un envio correcto limpia el campo y llama a onSent', async () => {
    const { textarea, onSent } = setup();
    type(textarea, 'mensaje');
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    await waitFor(() => expect(onSent).toHaveBeenCalledTimes(1));
    expect(textarea.value).toBe('');
  });

  it('con success:false muestra el mensaje bajo el campo y conserva el texto', async () => {
    const { textarea, onSent } = setup({
      chat_send: vi.fn(async () => ({ success: false, code: 'UNKNOWN_MENTION', message: 'No existe @fantasma' }))
    });
    type(textarea, 'hola @fantasma');
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    expect((await screen.findByRole('alert')).textContent).toBe('No existe @fantasma');
    expect(textarea.value).toBe('hola @fantasma');
    expect(onSent).not.toHaveBeenCalled();
  });

  it('sin message usa un texto por codigo', async () => {
    const { textarea } = setup({ chat_send: vi.fn(async () => ({ success: false, code: 'UNKNOWN_MENTION' })) });
    type(textarea, 'x');
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    expect((await screen.findByRole('alert')).textContent).toContain('no existe en este proyecto');
  });

  it('los resultados parciales se listan por destino', async () => {
    const { textarea, onSent } = setup({
      chat_send: vi.fn(async () => ({
        success: true,
        results: [
          { target: '@claude', ok: true, code: 'OK', message: '', msg_id: 'm1' },
          { target: '@orquestador', ok: false, code: 'HELD', message: 'Retenido por la pausa' }
        ]
      }))
    });
    type(textarea, '@claude @orquestador revisad');
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    const list = await screen.findByRole('list', { name: 'Destinos que no recibieron el mensaje' });
    expect(list.textContent).toContain('@orquestador');
    expect(list.textContent).toContain('Retenido por la pausa');
    expect(list.textContent).not.toContain('@claude');
    expect(onSent).toHaveBeenCalled();
  });

  it('mientras envia el boton esta deshabilitado', async () => {
    let finish;
    const { textarea } = setup({
      chat_send: vi.fn(() => new Promise((resolve) => { finish = resolve; }))
    });
    type(textarea, 'mensaje');
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    const busy = await screen.findByRole('button', { name: 'Enviando…' });
    expect(busy.disabled).toBe(true);
    finish({ success: true, results: [] });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enviar' })).toBeTruthy());
  });

  it('una excepcion del puente se muestra como texto', async () => {
    const { textarea } = setup({ chat_send: vi.fn(async () => { throw new Error('caido'); }) });
    type(textarea, 'hola');
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    expect((await screen.findByRole('alert')).textContent).toBe('caido');
  });

  it('sin chat_send en el puente informa y no lanza', () => {
    const { textarea } = setup({ chat_send: undefined });
    type(textarea, 'hola');
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    expect(screen.getByRole('alert').textContent).toContain('no permite enviar');
  });
});

describe('ChatComposer: contador de bytes', () => {
  it('no aparece por debajo del 80 % de 8192 bytes', () => {
    const { textarea } = setup();
    type(textarea, 'a'.repeat(6000));
    expect(screen.queryByText(/bytes/)).toBeNull();
  });

  it('aparece al pasar del 80 % y cuenta bytes UTF-8', () => {
    const { textarea } = setup();
    type(textarea, 'ñ'.repeat(3300));
    expect(screen.getByText('6600 / 8192 bytes')).toBeTruthy();
  });

  it('por encima del limite se marca y no permite enviar', () => {
    const { textarea, api } = setup();
    type(textarea, 'a'.repeat(8193));
    expect(screen.getByText(/demasiado largo/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Enviar' }).disabled).toBe(true);
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(api.chat_send).not.toHaveBeenCalled();
  });
});

describe('ChatComposer: menciones', () => {
  it('escribir @ abre la lista con las opciones', () => {
    const { textarea } = setup();
    type(textarea, '@');
    const list = screen.getByRole('listbox');
    expect(list.querySelectorAll('[role="option"]').length).toBe(3);
    expect(textarea.getAttribute('aria-expanded')).toBe('true');
    expect(textarea.getAttribute('aria-activedescendant')).toBeTruthy();
  });

  it('las flechas mueven la opcion activa y aria-activedescendant la sigue', () => {
    const { textarea } = setup();
    type(textarea, '@');
    const first = textarea.getAttribute('aria-activedescendant');
    fireEvent.keyDown(textarea, { key: 'ArrowDown' });
    const second = textarea.getAttribute('aria-activedescendant');
    expect(second).not.toBe(first);
    expect(document.getElementById(second).getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(textarea, { key: 'ArrowUp' });
    expect(textarea.getAttribute('aria-activedescendant')).toBe(first);
  });

  it('Enter elige la mencion en vez de enviar', async () => {
    const { textarea, api } = setup();
    type(textarea, '@cla');
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(textarea.value).toBe('@claude ');
    expect(api.chat_send).not.toHaveBeenCalled();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('Tab elige la mencion', () => {
    const { textarea } = setup();
    type(textarea, 'hola @orq');
    fireEvent.keyDown(textarea, { key: 'Tab' });
    expect(textarea.value).toBe('hola @orquestador ');
  });

  it('un clic en una opcion la elige sin quitar el foco del campo', () => {
    const { textarea } = setup();
    type(textarea, '@cl');
    const option = screen.getByRole('option', { name: /@claude/ });
    fireEvent.mouseDown(option);
    expect(textarea.value).toBe('@claude ');
  });

  it('Esc cierra la lista sin propagarse al resto (el panel no se cierra)', () => {
    const parentKeyDown = vi.fn();
    render(
      <div onKeyDown={parentKeyDown}>
        <ChatComposer api={{ chat_send: vi.fn() }} projectUuid="p1" participants={PARTICIPANTS} />
      </div>
    );
    const textarea = screen.getByRole('combobox', { name: 'Mensaje' });
    type(textarea, '@');
    expect(screen.getByRole('listbox')).toBeTruthy();
    fireEvent.keyDown(textarea, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(parentKeyDown).not.toHaveBeenCalled();
  });

  it('una direccion de correo no abre la lista', () => {
    const { textarea } = setup();
    type(textarea, 'escribe a@b');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('sin coincidencias no hay lista', () => {
    const { textarea } = setup();
    type(textarea, '@zzz');
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
