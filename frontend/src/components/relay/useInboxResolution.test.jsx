// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import React from 'react';
import { useInboxResolution } from './useInboxResolution';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Harness({ api, relay = { refresh: vi.fn(async () => {}) } }) {
  const { resolve, busyId, feedback, dialog } = useInboxResolution(api, relay);
  return (
    <div>
      <button type="button" data-testid="release" onClick={() => resolve('m-1', 'release')}>liberar</button>
      <button type="button" data-testid="discard" onClick={() => resolve('m-1', 'discard')}>descartar</button>
      <span data-testid="busy">{busyId ?? 'none'}</span>
      {feedback && <span data-testid="feedback" data-tone={feedback.tone}>{feedback.text}</span>}
      {dialog}
    </div>
  );
}

describe('useInboxResolution', () => {
  it('liberar llama a relay_resolve_parked, confirma el resultado y refresca la bandeja', async () => {
    const api = { relay_resolve_parked: vi.fn(async () => ({ success: true, state: 'queued' })) };
    const relay = { refresh: vi.fn(async () => {}) };
    render(<Harness api={api} relay={relay} />);
    fireEvent.click(screen.getByTestId('release'));
    await waitFor(() => expect(screen.getByTestId('feedback').getAttribute('data-tone')).toBe('ok'));
    expect(api.relay_resolve_parked).toHaveBeenCalledWith('m-1', 'release');
    expect(screen.getByTestId('feedback').textContent).toContain('ya está en la bandeja');
    expect(relay.refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('busy').textContent).toBe('none');
  });

  it('marca el mensaje como ocupado mientras el puente responde', async () => {
    let finish;
    const api = { relay_resolve_parked: vi.fn(() => new Promise((resolve) => { finish = resolve; })) };
    render(<Harness api={api} />);
    fireEvent.click(screen.getByTestId('release'));
    await waitFor(() => expect(screen.getByTestId('busy').textContent).toBe('m-1'));
    // Un segundo clic mientras hay una acción en curso no envía otra llamada.
    fireEvent.click(screen.getByTestId('discard'));
    expect(api.relay_resolve_parked).toHaveBeenCalledTimes(1);
    await act(async () => finish({ success: true }));
    await waitFor(() => expect(screen.getByTestId('busy').textContent).toBe('none'));
  });

  it('descartar pide la confirmacion propia de la app antes de llamar al puente', async () => {
    // El backend pide confirmar; al aceptar, ui_confirm_action ejecuta la accion y devuelve el resultado final.
    const api = {
      relay_resolve_parked: vi.fn(async () => ({ success: false, code: 'CONFIRMATION_REQUIRED', confirmation: { token: 'tok-1', title: 'Descartar', message: 'Texto exacto' } })),
      ui_confirm_action: vi.fn(async () => ({ success: true, code: 'OK' }))
    };
    render(<Harness api={api} />);
    fireEvent.click(screen.getByTestId('discard'));
    expect(await screen.findByRole('alertdialog')).toBeTruthy();
    expect(screen.getByTestId('app-confirm-message').textContent).toBe('Texto exacto');
    // El botón Confirmar se arma tras 600 ms (anti-clic accidental): esperamos a que esté activo.
    await waitFor(() => expect(screen.getByTestId('app-confirm-accept').disabled).toBe(false));
    fireEvent.click(screen.getByTestId('app-confirm-accept'));
    await waitFor(() => expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-1', true, false));
    await waitFor(() => expect(screen.getByTestId('feedback').textContent).toContain('Mensaje descartado'));
    expect(api.relay_resolve_parked).toHaveBeenCalledTimes(1);
    expect(api.relay_resolve_parked).toHaveBeenCalledWith('m-1', 'discard');
  });

  it('si el usuario cancela la confirmacion no hay cambios y se explica', async () => {
    const api = {
      relay_resolve_parked: vi.fn(async () => ({ success: false, code: 'CONFIRMATION_REQUIRED', confirmation: { token: 'tok-2', message: 'x' } })),
      ui_confirm_action: vi.fn(async () => ({ success: false, code: 'CONFIRMATION_DECLINED' }))
    };
    render(<Harness api={api} />);
    fireEvent.click(screen.getByTestId('discard'));
    await screen.findByRole('alertdialog');
    fireEvent.click(screen.getByTestId('app-confirm-cancel'));
    await waitFor(() => expect(screen.getByTestId('feedback').getAttribute('data-tone')).toBe('info'));
    expect(screen.getByTestId('feedback').textContent).toContain('Rechazó el diálogo');
    expect(api.relay_resolve_parked).toHaveBeenCalledTimes(1);
  });

  it('un error del puente se muestra con causa y accion, y libera el estado ocupado', async () => {
    const api = { relay_resolve_parked: vi.fn(async () => { throw new Error('puente caido'); }) };
    const relay = { refresh: vi.fn(async () => {}) };
    render(<Harness api={api} relay={relay} />);
    fireEvent.click(screen.getByTestId('release'));
    await waitFor(() => expect(screen.getByTestId('feedback').getAttribute('data-tone')).toBe('error'));
    expect(screen.getByTestId('feedback').textContent).toContain('No se pudo liberar el mensaje');
    expect(screen.getByTestId('feedback').textContent).toContain('puente caido');
    expect(screen.getByTestId('busy').textContent).toBe('none');
    expect(relay.refresh).toHaveBeenCalledTimes(1);
  });

  it('sin el metodo del puente no hace nada', async () => {
    render(<Harness api={{}} />);
    fireEvent.click(screen.getByTestId('release'));
    await act(async () => {});
    expect(screen.queryByTestId('feedback')).toBeNull();
    expect(screen.getByTestId('busy').textContent).toBe('none');
  });
});
