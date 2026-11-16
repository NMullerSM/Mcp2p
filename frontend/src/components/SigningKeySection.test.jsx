// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { SigningKeySection } from './SigningKeySection';
import { SettingsDrawer } from './SettingsDrawer';

const PUB = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=';
const status = (encrypted, locked) => ({ success: true, encrypted, locked, public_key: PUB });

function makeApi(initial, overrides = {}) {
  return {
    get_signing_key_status: vi.fn(() => Promise.resolve(initial)),
    set_signing_key_passphrase: vi.fn(() => Promise.resolve(status(true, false))),
    unlock_signing_key: vi.fn(() => Promise.resolve(status(true, false))),
    lock_signing_key: vi.fn(() => Promise.resolve(status(true, true))),
    ...overrides
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SigningKeySection', () => {
  it('no se muestra si el puente no expone el estado de la clave', () => {
    const { container } = render(<SigningKeySection api={{}} />);
    expect(container.firstChild).toBeNull();
  });

  it('estado sin cifrar: ofrece fijar frase, exige repetirla y avisa de que no hay recuperación', async () => {
    const api = makeApi({ ...status(false, false) });
    api.set_signing_key_passphrase = vi.fn(() => Promise.resolve({ ...status(true, false) }));
    render(<SigningKeySection api={api} />);

    expect((await screen.findByTestId('signing-key-state')).textContent).toBe('Sin cifrar');
    fireEvent.click(screen.getByText('Fijar frase'));
    expect(screen.getByTestId('signing-key-no-recovery-warning').textContent).toContain('no hay recuperación');

    const first = screen.getByLabelText('Frase nueva');
    const second = screen.getByLabelText('Repite la frase');
    expect(first.getAttribute('type')).toBe('password');
    expect(first.getAttribute('autocomplete')).toBe('off');
    expect(second.getAttribute('type')).toBe('password');

    // frases distintas: no llama al backend
    fireEvent.change(first, { target: { value: 'una frase bastante larga' } });
    fireEvent.change(second, { target: { value: 'otra frase bastante larga' } });
    fireEvent.click(screen.getByText('Cifrar clave'));
    expect(screen.getByRole('alert').textContent).toBe('Las dos frases no coinciden.');
    expect(api.set_signing_key_passphrase).not.toHaveBeenCalled();

    // frase corta: tampoco
    fireEvent.change(first, { target: { value: 'corta' } });
    fireEvent.change(second, { target: { value: 'corta' } });
    fireEvent.click(screen.getByText('Cifrar clave'));
    expect(screen.getByRole('alert').textContent).toContain('al menos 10 caracteres');
    expect(api.set_signing_key_passphrase).not.toHaveBeenCalled();

    // coinciden: llama, pasa a desbloqueada y limpia los campos
    fireEvent.change(first, { target: { value: 'una frase bastante larga' } });
    fireEvent.change(second, { target: { value: 'una frase bastante larga' } });
    fireEvent.click(screen.getByText('Cifrar clave'));
    await waitFor(() => expect(api.set_signing_key_passphrase).toHaveBeenCalledWith('una frase bastante larga'));
    await waitFor(() => expect(screen.getByTestId('signing-key-state').textContent).toBe('Desbloqueada'));
    expect(screen.queryByLabelText('Frase nueva')).toBeNull();
  });

  it('estado cifrada y bloqueada: desbloquear con la frase y limpiar el campo', async () => {
    const api = makeApi(status(true, true));
    render(<SigningKeySection api={api} />);

    expect((await screen.findByTestId('signing-key-state')).textContent).toBe('Cifrada y bloqueada');
    fireEvent.click(screen.getByText('Desbloquear'));
    const input = screen.getByLabelText('Frase de la clave');
    expect(input.getAttribute('type')).toBe('password');
    fireEvent.change(input, { target: { value: 'una frase bastante larga' } });
    fireEvent.submit(input.closest('form'));

    await waitFor(() => expect(api.unlock_signing_key).toHaveBeenCalledWith('una frase bastante larga'));
    await waitFor(() => expect(screen.getByTestId('signing-key-state').textContent).toBe('Desbloqueada'));
  });

  it('BAD_PASSPHRASE al desbloquear muestra el error, mantiene el formulario y vacía la frase', async () => {
    const api = makeApi(status(true, true), {
      unlock_signing_key: vi.fn(() => Promise.resolve({ success: false, code: 'BAD_PASSPHRASE', error: 'Frase de paso incorrecta.' }))
    });
    render(<SigningKeySection api={api} />);
    await screen.findByTestId('signing-key-state');
    fireEvent.click(screen.getByText('Desbloquear'));
    const input = screen.getByLabelText('Frase de la clave');
    fireEvent.change(input, { target: { value: 'frase equivocada!' } });
    fireEvent.submit(input.closest('form'));

    expect((await screen.findByRole('alert')).textContent).toBe('Frase de paso incorrecta.');
    await waitFor(() => expect(screen.getByLabelText('Frase de la clave').value).toBe(''));
    expect(screen.getByTestId('signing-key-state').textContent).toBe('Cifrada y bloqueada');
  });

  it('estado desbloqueada: "Bloquear ahora" llama a lock_signing_key y pasa a bloqueada', async () => {
    const api = makeApi(status(true, false));
    render(<SigningKeySection api={api} />);

    expect((await screen.findByTestId('signing-key-state')).textContent).toBe('Desbloqueada');
    fireEvent.click(screen.getByText('Bloquear ahora'));
    await waitFor(() => expect(api.lock_signing_key).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('signing-key-state').textContent).toBe('Cifrada y bloqueada'));
  });

  it('aparece en la pestaña Terminales & MCP de Configuración y no en GitHub Auth', async () => {
    const api = {
      ...makeApi(status(false, false)),
      get_system_info: vi.fn(() => Promise.resolve({ success: true, active_node: 'n1' })),
      get_github_status: vi.fn(() => Promise.resolve({ success: true, github: { authenticated: false } })),
      get_signaling_server_url: vi.fn(() => Promise.resolve(''))
    };
    const { rerender } = render(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={null} projectsList={[]} initialTab="github" />
    );
    // GitHub Auth ya no lleva la firma humana: pertenece a la seccion de terminales.
    await screen.findByText('Autenticación Dual de GitHub');
    expect(screen.queryByTestId('signing-key-state')).toBeNull();

    rerender(
      <SettingsDrawer isOpen={true} onClose={vi.fn()} api={api} activeProject={null} projectsList={[]} initialTab="mcp" />
    );
    expect((await screen.findByTestId('signing-key-state')).textContent).toBe('Sin cifrar');
  });
});
