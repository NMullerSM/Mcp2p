// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { JoinRequestPending } from './JoinRequestPending';

const PROJECT = { project_uuid: 'proj_abc123', name: 'Proyecto Demo' };
const REQUEST_CODE = 'eyJhbGciOiJ-request-code-token-xyz';

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn(() => Promise.resolve()) }
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('JoinRequestPending', () => {
  it('renderiza el nombre del proyecto y el código de solicitud', () => {
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={vi.fn()} />);
    expect(screen.getByText('Proyecto Demo')).toBeTruthy();
    expect(screen.getByText(REQUEST_CODE)).toBeTruthy();
  });

  it('copia el código de solicitud al portapapeles y muestra feedback', () => {
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={vi.fn()} />);
    const copyButton = screen.getByText('Copiar Código');
    fireEvent.click(copyButton);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(REQUEST_CODE);
    expect(screen.getByText('Copiado')).toBeTruthy();
  });

  it('invoca onRetry al pulsar "Reintentar"', async () => {
    const onRetry = vi.fn(() => Promise.resolve());
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={onRetry} />);
    fireEvent.click(screen.getByText('Reintentar'));
    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1));
  });

  it('muestra el mensaje que resuelve onRetry (p. ej. "todavía pendiente")', async () => {
    const onRetry = vi.fn(() => Promise.resolve('Todavía pendiente de aprobación por el dueño.'));
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={onRetry} />);
    fireEvent.click(screen.getByText('Reintentar'));
    const statusEl = await screen.findByTestId('join-request-status-message');
    expect(statusEl.textContent).toBe('Todavía pendiente de aprobación por el dueño.');
  });

  it('muestra el mensaje terminal de "no_remote_repos" sin sugerir reintentar indefinidamente', async () => {
    const terminalMessage = 'El dueño del proyecto todavía no ha publicado sus repositorios remotos.';
    const onRetry = vi.fn(() => Promise.resolve(terminalMessage));
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={onRetry} />);
    fireEvent.click(screen.getByText('Reintentar'));
    const statusEl = await screen.findByTestId('join-request-status-message');
    expect(statusEl.textContent).toBe(terminalMessage);
  });

  it('no deja el mensaje de estado previo visible al reintentar de nuevo', async () => {
    const onRetry = vi.fn()
      .mockResolvedValueOnce('Todavía pendiente de aprobación por el dueño.')
      .mockResolvedValueOnce(undefined);
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={onRetry} />);

    fireEvent.click(screen.getByText('Reintentar'));
    await screen.findByTestId('join-request-status-message');

    fireEvent.click(screen.getByText('Reintentar'));
    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId('join-request-status-message')).toBeNull();
  });

  it('deshabilita el botón "Reintentar" mientras la llamada está en curso', async () => {
    let resolvePromise;
    const onRetry = vi.fn(() => new Promise((resolve) => { resolvePromise = resolve; }));
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={onRetry} />);

    const retryButton = screen.getByText('Reintentar').closest('button');
    fireEvent.click(retryButton);
    expect(retryButton.disabled).toBe(true);

    resolvePromise();
    await waitFor(() => expect(retryButton.disabled).toBe(false));
  });

  it('no llama a navigator.clipboard.writeText cuando no hay requestCode', () => {
    render(<JoinRequestPending project={PROJECT} requestCode="" onRetry={vi.fn()} />);
    fireEvent.click(screen.getByText('Copiar Código'));
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
  });

  it('invoca onBack al pulsar "Volver a Proyectos", dando una salida distinta de un retry exitoso', () => {
    const onBack = vi.fn();
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={vi.fn()} onBack={onBack} />);
    fireEvent.click(screen.getByText('Volver a Proyectos'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('avisa honestamente de que un rechazo no se notifica (hallazgo I2)', () => {
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={vi.fn()} />);
    const note = screen.getByTestId('join-request-rejection-note');
    expect(note.textContent).toContain('no te lo notificará');
    expect(note.textContent).toContain('directamente con el dueño');
  });

  it('no renderiza el botón "Volver a Proyectos" cuando no se provee onBack', () => {
    render(<JoinRequestPending project={PROJECT} requestCode={REQUEST_CODE} onRetry={vi.fn()} />);
    expect(screen.queryByText('Volver a Proyectos')).toBeNull();
  });
});
