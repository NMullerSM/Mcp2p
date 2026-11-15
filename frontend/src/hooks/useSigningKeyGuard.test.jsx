// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React, { useState } from 'react';
import { useSigningKeyGuard, isKeyLocked } from './useSigningKeyGuard';

const LOCKED = { success: false, code: 'KEY_LOCKED', error: 'KEY_LOCKED: la clave está bloqueada' };

// Mini vista que firma a través del hook, como HumanApprovalsView / ProjectWorkspace.
function Harness({ api, action }) {
  const { withKeyUnlock, unlockDialog } = useSigningKeyGuard(api);
  const [result, setResult] = useState(null);
  return (
    <div>
      <button onClick={async () => setResult(await withKeyUnlock(action))}>firmar</button>
      <div data-testid="result">{result ? JSON.stringify(result) : ''}</div>
      {unlockDialog}
    </div>
  );
}

const phraseInput = () => screen.getByLabelText('Frase de la clave de firma');

async function submitPhrase(value) {
  fireEvent.change(phraseInput(), { target: { value } });
  fireEvent.click(screen.getByText('Desbloquear y continuar'));
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('isKeyLocked', () => {
  it('reconoce el code y el marcador textual, y nunca un éxito', () => {
    expect(isKeyLocked(LOCKED)).toBe(true);
    expect(isKeyLocked({ success: false, error: 'Error al unirse: KEY_LOCKED: bloqueada' })).toBe(true);
    expect(isKeyLocked({ success: false, code: 'BAD_PASSPHRASE' })).toBe(false);
    expect(isKeyLocked({ success: true, code: 'KEY_LOCKED' })).toBe(false);
    expect(isKeyLocked({ success: false, error: 'otro error' })).toBe(false);
    expect(isKeyLocked(null)).toBe(false);
  });
});

describe('useSigningKeyGuard', () => {
  it('sin KEY_LOCKED devuelve la respuesta tal cual y no abre diálogo', async () => {
    const action = vi.fn(() => Promise.resolve({ success: true }));
    const api = { unlock_signing_key: vi.fn() };
    render(<Harness api={api} action={action} />);
    fireEvent.click(screen.getByText('firmar'));
    await waitFor(() => expect(screen.getByTestId('result').textContent).toBe('{"success":true}'));
    expect(action).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('unlock-signing-key-dialog')).toBeNull();
    expect(api.unlock_signing_key).not.toHaveBeenCalled();
  });

  it('KEY_LOCKED abre el diálogo, desbloquea y reintenta la acción una sola vez', async () => {
    const action = vi.fn()
      .mockResolvedValueOnce(LOCKED)
      .mockResolvedValueOnce({ success: true, merged: true });
    const api = { unlock_signing_key: vi.fn(() => Promise.resolve({ success: true, locked: false })) };
    render(<Harness api={api} action={action} />);

    fireEvent.click(screen.getByText('firmar'));
    await screen.findByTestId('unlock-signing-key-dialog');
    expect(action).toHaveBeenCalledTimes(1);

    await submitPhrase('mi frase secreta larga');
    await waitFor(() => expect(api.unlock_signing_key).toHaveBeenCalledWith('mi frase secreta larga'));
    await waitFor(() => expect(screen.getByTestId('result').textContent).toBe('{"success":true,"merged":true}'));
    expect(action).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('unlock-signing-key-dialog')).toBeNull();
  });

  it('si el reintento vuelve a dar KEY_LOCKED no entra en bucle: devuelve esa respuesta', async () => {
    const action = vi.fn(() => Promise.resolve(LOCKED));
    const api = { unlock_signing_key: vi.fn(() => Promise.resolve({ success: true })) };
    render(<Harness api={api} action={action} />);
    fireEvent.click(screen.getByText('firmar'));
    await screen.findByTestId('unlock-signing-key-dialog');
    await submitPhrase('mi frase secreta larga');
    await waitFor(() => expect(screen.getByTestId('result').textContent).toContain('KEY_LOCKED'));
    expect(action).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('unlock-signing-key-dialog')).toBeNull();
  });

  it('BAD_PASSPHRASE se muestra en el diálogo, que sigue abierto, sin reintentar la acción', async () => {
    const action = vi.fn(() => Promise.resolve(LOCKED));
    const api = {
      unlock_signing_key: vi.fn(() => Promise.resolve({ success: false, code: 'BAD_PASSPHRASE', error: 'Frase de paso incorrecta.' }))
    };
    render(<Harness api={api} action={action} />);
    fireEvent.click(screen.getByText('firmar'));
    await screen.findByTestId('unlock-signing-key-dialog');

    await submitPhrase('frase equivocada');
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Frase de paso incorrecta.');
    expect(screen.getByTestId('unlock-signing-key-dialog')).toBeTruthy();
    expect(action).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('result').textContent).toBe('');
  });

  it('la frase se limpia del input tras el intento (error) y es un campo password sin autocompletado', async () => {
    const api = {
      unlock_signing_key: vi.fn(() => Promise.resolve({ success: false, code: 'BAD_PASSPHRASE', error: 'Frase de paso incorrecta.' }))
    };
    render(<Harness api={api} action={() => Promise.resolve(LOCKED)} />);
    fireEvent.click(screen.getByText('firmar'));
    await screen.findByTestId('unlock-signing-key-dialog');

    const input = phraseInput();
    expect(input.getAttribute('type')).toBe('password');
    expect(input.getAttribute('autocomplete')).toBe('off');

    await submitPhrase('frase equivocada');
    await screen.findByRole('alert');
    await waitFor(() => expect(phraseInput().value).toBe(''));
  });

  it('la frase no llega a localStorage ni a sessionStorage', async () => {
    const api = { unlock_signing_key: vi.fn(() => Promise.resolve({ success: true })) };
    const action = vi.fn().mockResolvedValueOnce(LOCKED).mockResolvedValueOnce({ success: true });
    render(<Harness api={api} action={action} />);
    fireEvent.click(screen.getByText('firmar'));
    await screen.findByTestId('unlock-signing-key-dialog');
    await submitPhrase('frase-muy-secreta-123');
    await waitFor(() => expect(screen.getByTestId('result').textContent).toContain('success'));
    const dump = JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage });
    expect(dump).not.toContain('frase-muy-secreta-123');
  });

  it('cancelar resuelve con cancelled y no reintenta ni llama a unlock', async () => {
    const action = vi.fn(() => Promise.resolve(LOCKED));
    const api = { unlock_signing_key: vi.fn() };
    render(<Harness api={api} action={action} />);
    fireEvent.click(screen.getByText('firmar'));
    await screen.findByTestId('unlock-signing-key-dialog');
    fireEvent.click(screen.getByText('Cancelar'));
    await waitFor(() => expect(JSON.parse(screen.getByTestId('result').textContent).cancelled).toBe(true));
    expect(action).toHaveBeenCalledTimes(1);
    expect(api.unlock_signing_key).not.toHaveBeenCalled();
    expect(screen.queryByTestId('unlock-signing-key-dialog')).toBeNull();
  });
});
