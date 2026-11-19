// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import React from 'react';
import { DeliverySettingsSection } from './DeliverySettingsSection';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const OVERVIEW = {
  success: true,
  default_mode: 'inbox',
  batch_default: false,
  verified: [
    { cli_id: 'claude', display_name: 'Claude Code', platform: 'linux', verified: true },
    { cli_id: 'codex', display_name: 'Codex', platform: 'darwin', verified: false }
  ]
};

describe('DeliverySettingsSection', () => {
  it('select_changes_default_and_reverts_on_failure', async () => {
    const api = {
      relay_get_delivery_overview: vi.fn(async () => OVERVIEW),
      relay_set_delivery_default: vi.fn(async (mode) => (mode === 'direct'
        ? { success: true, default_mode: 'direct' }
        : { success: false, message: 'No se pudo guardar.' }))
    };
    render(<DeliverySettingsSection api={api} />);
    const select = await screen.findByTestId('delivery-default-select');
    await waitFor(() => expect(select.disabled).toBe(false));
    expect(select.value).toBe('inbox');

    fireEvent.change(select, { target: { value: 'direct' } });
    await waitFor(() => expect(api.relay_set_delivery_default).toHaveBeenCalledWith('direct'));
    await waitFor(() => expect(select.disabled).toBe(false));
    expect(select.value).toBe('direct');
    expect((await screen.findByTestId('delivery-settings-feedback')).textContent).toContain('guardada');

    // Un fallo del puente devuelve la seleccion al valor anterior y muestra el motivo.
    fireEvent.change(select, { target: { value: 'inbox' } });
    await waitFor(() => expect(select.value).toBe('direct'));
    expect((await screen.findByTestId('delivery-settings-feedback')).textContent).toContain('No se pudo guardar');
  });

  it('table_lists_verified_and_unverified', async () => {
    const api = {
      relay_get_delivery_overview: vi.fn(async () => OVERVIEW),
      relay_set_delivery_default: vi.fn(async () => ({ success: true }))
    };
    render(<DeliverySettingsSection api={api} />);
    const table = await screen.findByTestId('delivery-verified-table');
    const claude = within(table).getByTestId('delivery-verified-row-claude');
    expect(claude.textContent).toContain('Claude Code');
    expect(claude.textContent).toContain('Linux');
    expect(claude.textContent).toContain('Verificada');
    const codex = within(table).getByTestId('delivery-verified-row-codex');
    expect(codex.textContent).toContain('macOS');
    expect(codex.textContent).toContain('No verificada');
  });

  it('renders_nothing_without_api_methods', () => {
    const { container } = render(<DeliverySettingsSection api={{ relay_get_delivery_overview: vi.fn() }} />);
    expect(container.textContent).toBe('');
    const { container: empty } = render(<DeliverySettingsSection api={{}} />);
    expect(empty.textContent).toBe('');
  });
});
