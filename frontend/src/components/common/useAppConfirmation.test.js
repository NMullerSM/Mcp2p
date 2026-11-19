// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act, waitFor } from '@testing-library/react';
import React, { useState } from 'react';
import { useAppConfirmation } from './useAppConfirmation';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function Harness({ api, action }) {
  const { dialog, run } = useAppConfirmation(api);
  const [result, setResult] = useState(null);
  return React.createElement(
    'div',
    null,
    React.createElement(
      'button',
      {
        type: 'button',
        'data-testid': 'run-btn',
        onClick: async () => {
          const out = await run(action);
          setResult(out);
        }
      },
      'Ejecutar'
    ),
    result && React.createElement('div', { 'data-testid': 'run-result' }, JSON.stringify(result)),
    dialog
  );
}

describe('useAppConfirmation', () => {
  it('devuelve la respuesta directa cuando no hace falta confirmación', async () => {
    const api = { ui_confirm_action: vi.fn() };
    const action = vi.fn(async () => ({ success: true, code: 'OK', worker_mode: true }));

    render(React.createElement(Harness, { api, action }));
    fireEvent.click(screen.getByTestId('run-btn'));

    await waitFor(() => expect(screen.getByTestId('run-result')).toBeTruthy());
    expect(JSON.parse(screen.getByTestId('run-result').textContent)).toEqual({
      success: true,
      code: 'OK',
      worker_mode: true
    });
    expect(api.ui_confirm_action).not.toHaveBeenCalled();
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('ante CONFIRMATION_REQUIRED abre AppConfirmDialog y al confirmar envía ui_confirm_action con remember', async () => {
    vi.useFakeTimers();
    const api = {
      ui_confirm_action: vi.fn(async (token, accepted, remember) => ({
        success: true,
        code: 'OK',
        token,
        accepted,
        remember
      }))
    };
    const action = vi.fn(async () => ({
      success: false,
      code: 'CONFIRMATION_REQUIRED',
      confirmation: {
        token: 'tok-hook-1',
        action_id: 'auto_relay.enable',
        title: 'Activar Auto-Relay',
        message: 'Mensaje exacto de Python',
        rememberable: true,
        expires_in_s: 120
      }
    }));

    render(React.createElement(Harness, { api, action }));
    fireEvent.click(screen.getByTestId('run-btn'));

    await act(async () => {});
    expect(screen.getByRole('alertdialog')).toBeTruthy();
    expect(screen.getByTestId('app-confirm-message').textContent).toBe('Mensaje exacto de Python');

    fireEvent.click(screen.getByTestId('app-confirm-remember'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    fireEvent.click(screen.getByTestId('app-confirm-accept'));
    await act(async () => {});

    expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-hook-1', true, true);
    expect(JSON.parse(screen.getByTestId('run-result').textContent)).toEqual({
      success: true,
      code: 'OK',
      token: 'tok-hook-1',
      accepted: true,
      remember: true
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('al cancelar notifica ui_confirm_action(token, false, false) y devuelve CONFIRMATION_DECLINED', async () => {
    const api = {
      ui_confirm_action: vi.fn(async () => ({
        success: false,
        code: 'CONFIRMATION_DECLINED',
        message: 'Rechazada'
      }))
    };
    const action = vi.fn(async () => ({
      success: false,
      code: 'CONFIRMATION_REQUIRED',
      confirmation: {
        token: 'tok-cancel-1',
        action_id: 'relay.discard_parked',
        title: 'Descartar',
        message: 'Texto',
        rememberable: false,
        expires_in_s: 120
      }
    }));

    render(React.createElement(Harness, { api, action }));
    fireEvent.click(screen.getByTestId('run-btn'));
    await waitFor(() => expect(screen.getByRole('alertdialog')).toBeTruthy());

    fireEvent.click(screen.getByTestId('app-confirm-cancel'));
    await waitFor(() => expect(screen.getByTestId('run-result')).toBeTruthy());

    expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-cancel-1', false, false);
    expect(JSON.parse(screen.getByTestId('run-result').textContent).code).toBe('CONFIRMATION_DECLINED');
  });
});
