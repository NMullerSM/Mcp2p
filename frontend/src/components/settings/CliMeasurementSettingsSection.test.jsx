// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import React from 'react';
import { CliMeasurementSettingsSection } from './CliMeasurementSettingsSection';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function item(overrides = {}) {
  return {
    cli_id: 'claude',
    display_name: 'Claude Code',
    installed: true,
    state: 'not_measured',
    measured_at: null,
    cli_version: null,
    paste_mode: null,
    confirm_event: null,
    reason_code: null,
    reason_text: null,
    restart_needed: false,
    ...overrides
  };
}

function snapshot(items, running = null) {
  return { success: true, platform: 'linux', running, items };
}

const RUNNING = {
  cli_id: 'claude',
  step: 'paste',
  steps_done: 2,
  steps_total: 5,
  message: 'Pegando el mensaje de prueba',
  started_ms: 1
};

describe('CliMeasurementSettingsSection', () => {
  it('renders_nothing_without_api_methods', () => {
    const { container } = render(<CliMeasurementSettingsSection api={{ pty_get_cli_measurements: vi.fn() }} />);
    expect(container.textContent).toBe('');
    const { container: empty } = render(<CliMeasurementSettingsSection api={{}} />);
    expect(empty.textContent).toBe('');
  });

  it('lists_items_with_state_texts', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([
        item({ state: 'measured', measured_at: '2026-11-10', cli_version: '1.2.3' }),
        item({ cli_id: 'codex', display_name: 'Codex', state: 'not_measured' }),
        item({ cli_id: 'opencode', display_name: 'OpenCode', state: 'stale' }),
        item({ cli_id: 'antigravity', display_name: 'Antigravity', state: 'unsupported' }),
        item({ cli_id: 'gemini', display_name: 'Gemini', installed: false, state: 'not_measured' })
      ])),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    const claude = await screen.findByTestId('measure-status-claude');
    expect(claude.textContent).toContain('Medido y activo');
    expect(claude.textContent).toContain('2026-11-10');
    expect(claude.textContent).toContain('1.2.3');
    expect(screen.getByTestId('measure-claude').textContent).toBe('Volver a medir');
    expect(screen.getByTestId('forget-claude')).toBeTruthy();

    expect(screen.getByTestId('measure-status-codex').textContent).toContain('Sin medir');
    expect(screen.getByTestId('measure-codex').textContent).toBe('Medir');
    expect(screen.queryByTestId('forget-codex')).toBeNull();

    expect(screen.getByTestId('measure-status-opencode').textContent).toContain('Hay que volver a medir');
    expect(screen.getByTestId('forget-opencode')).toBeTruthy();

    expect(screen.getByTestId('measure-status-antigravity').textContent).toContain('No admitido');
    expect(screen.getByTestId('measure-antigravity').disabled).toBe(true);

    expect(screen.getByTestId('measure-status-gemini').textContent).toContain('No instalado');
    expect(screen.getByTestId('measure-gemini').disabled).toBe(true);
  });

  it('measure_button_runs_through_confirmation_then_polls', async () => {
    let phase = 'idle';
    let polls = 0;
    const api = {
      pty_get_cli_measurements: vi.fn(async () => {
        if (phase === 'idle') return snapshot([item()]);
        if (phase === 'running') {
          polls += 1;
          if (polls > 1) {
            phase = 'done';
            return snapshot([item({ state: 'measured', measured_at: '2026-11-19', cli_version: '2.0.0' })]);
          }
          return snapshot([item()], RUNNING);
        }
        return snapshot([item({ state: 'measured', measured_at: '2026-11-19', cli_version: '2.0.0' })]);
      }),
      pty_measure_cli: vi.fn(async () => ({
        success: false,
        code: 'CONFIRMATION_REQUIRED',
        confirmation: { token: 'tok-1', title: 'Medir Claude Code', message: 'Se abrirá una terminal temporal.', rememberable: false }
      })),
      ui_confirm_action: vi.fn(async () => {
        phase = 'running';
        return { success: true, started: true };
      }),
      pty_cancel_cli_measurement: vi.fn(async () => ({ success: true })),
      pty_forget_cli_measurement: vi.fn(async () => ({ success: true }))
    };
    render(<CliMeasurementSettingsSection api={api} />);

    fireEvent.click(await screen.findByTestId('measure-claude'));
    expect(api.pty_measure_cli).toHaveBeenCalledWith('claude');

    const accept = await screen.findByTestId('app-confirm-accept');
    await waitFor(() => expect(accept.disabled).toBe(false));
    fireEvent.click(accept);

    await waitFor(() => expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-1', true, false));
    expect(await screen.findByTestId('measure-running')).toBeTruthy();
    expect(screen.getByTestId('measure-progress')).toBeTruthy();

    // El sondeo (1,5 s) recoge el final de la medicion.
    await waitFor(() => expect(screen.getByTestId('measure-status-claude').textContent).toContain('Medido y activo'), { timeout: 4000 });
    expect(screen.queryByTestId('measure-running')).toBeNull();
    expect(api.pty_get_cli_measurements.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it('declined_confirmation_is_not_an_error', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([item()])),
      pty_measure_cli: vi.fn(async () => ({
        success: false,
        code: 'CONFIRMATION_REQUIRED',
        confirmation: { token: 'tok-2', title: 'Medir', message: 'Confirmar', rememberable: false }
      })),
      ui_confirm_action: vi.fn(async () => ({ success: true })),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    fireEvent.click(await screen.findByTestId('measure-claude'));
    const cancel = await screen.findByTestId('app-confirm-cancel');
    fireEvent.click(cancel);

    await waitFor(() => expect(screen.queryByTestId('app-confirm-dialog')).toBeNull());
    expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-2', false, false);
    expect(screen.queryByTestId('measure-feedback')).toBeNull();
  });

  it('running_disables_buttons_and_shows_progress_and_cancel', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([
        item(),
        item({ cli_id: 'codex', display_name: 'Codex' })
      ], RUNNING)),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(async () => ({ success: true })),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    const row = await screen.findByTestId('measure-row-claude');
    expect(within(row).getByText('Pegando el mensaje de prueba')).toBeTruthy();
    const progress = within(row).getByTestId('measure-progress');
    expect(Number(progress.value)).toBe(2);
    expect(Number(progress.max)).toBe(5);

    expect(screen.getByTestId('measure-claude').disabled).toBe(true);
    expect(screen.getByTestId('measure-codex').disabled).toBe(true);

    fireEvent.click(screen.getByTestId('measure-cancel'));
    await waitFor(() => expect(api.pty_cancel_cli_measurement).toHaveBeenCalledTimes(1));
  });

  it('forget_calls_api_and_refreshes', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([
        item({ state: 'measured', measured_at: '2026-11-10', cli_version: '1.2.3' })
      ])),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn(async () => ({ success: true }))
    };
    render(<CliMeasurementSettingsSection api={api} />);

    fireEvent.click(await screen.findByTestId('forget-claude'));
    await waitFor(() => expect(api.pty_forget_cli_measurement).toHaveBeenCalledWith('claude'));
    await waitFor(() => expect(api.pty_get_cli_measurements.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect((await screen.findByTestId('measure-feedback')).textContent).toContain('olvidada');
  });

  it('restart_note_shown', async () => {
    const withRestart = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([item({ state: 'measured', restart_needed: true })])),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={withRestart} />);
    const note = await screen.findByTestId('measure-restart-note');
    expect(note.textContent).toContain('Cierra y vuelve a abrir las terminales de este CLI');
    cleanup();

    const withoutRestart = {
      ...withRestart,
      pty_get_cli_measurements: vi.fn(async () => snapshot([item({ state: 'measured', restart_needed: false })]))
    };
    render(<CliMeasurementSettingsSection api={withoutRestart} />);
    await screen.findByTestId('measure-row-claude');
    expect(screen.queryByTestId('measure-restart-note')).toBeNull();
  });

  it('failed_shows_reason_text', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([
        item({
          state: 'failed',
          reason_code: 'PASTE_NOT_ACCEPTED',
          reason_text: 'El CLI no acepta mensajes pegados en una sola línea.'
        })
      ])),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    const status = await screen.findByTestId('measure-status-claude');
    expect(status.textContent).toContain('No se pudo medir');
    expect(screen.getByTestId('measure-reason-claude').textContent).toBe('El CLI no acepta mensajes pegados en una sola línea.');
    expect(screen.getByTestId('measure-claude').textContent).toBe('Volver a medir');
    expect(screen.queryByTestId('forget-claude')).toBeNull();
  });

  it('forwards_project_uuid_when_present', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([item()])),
      pty_measure_cli: vi.fn(async () => ({ success: true, started: true })),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} projectUuid="proj-123" />);
    fireEvent.click(await screen.findByTestId('measure-claude'));
    await waitFor(() => expect(api.pty_measure_cli).toHaveBeenCalledWith('claude', 'proj-123'));
  });

  it('dot_has_accessible_label_for_each_state', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([
        item({ cli_id: 'claude', display_name: 'Claude Code', state: 'measured', dot: 'green', dot_label: 'Medido y activo' }),
        item({ cli_id: 'opencode', display_name: 'OpenCode', state: 'stale', dot: 'yellow', dot_label: 'Hay que volver a medir' }),
        item({
          cli_id: 'codex',
          display_name: 'Codex',
          state: 'failed',
          dot: 'red',
          dot_label: 'No se pudo medir',
          reason_text: 'El CLI no acepta mensajes pegados en una sola línea.'
        }),
        item({ cli_id: 'gemini', display_name: 'Gemini', state: 'not_measured', dot: 'gray', dot_label: 'Sin medir' })
      ])),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    await screen.findByTestId('measure-row-claude');
    expect(screen.getByRole('img', { name: 'Medido y activo' }).getAttribute('data-dot')).toBe('green');
    expect(screen.getByRole('img', { name: 'Hay que volver a medir' }).getAttribute('data-dot')).toBe('yellow');
    expect(screen.getByRole('img', { name: 'No se pudo medir' }).getAttribute('data-dot')).toBe('red');
    expect(screen.getByRole('img', { name: 'Sin medir' }).getAttribute('data-dot')).toBe('gray');
    // El motivo del fallo sigue en texto: el color no transmite el estado por sí solo.
    expect(screen.getByTestId('measure-reason-codex')).toBeTruthy();
  });

  it('dot_is_omitted_when_bridge_sends_no_dot', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([item({ state: 'measured' })])),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);
    await screen.findByTestId('measure-row-claude');
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('auto_toggle_calls_bridge_ticks_and_refreshes_when_enabled', async () => {
    let autoEnabled = false;
    const api = {
      pty_get_cli_measurements: vi.fn(async () => ({ ...snapshot([item()]), auto_enabled: autoEnabled })),
      pty_set_cli_measure_auto: vi.fn(async (enabled) => {
        autoEnabled = enabled;
        return { success: true, auto_enabled: enabled };
      }),
      pty_auto_measure_tick: vi.fn(async () => ({ success: true, started: null, queued: 0, reason: 'idle' })),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    const toggle = await screen.findByTestId('measure-auto-toggle');
    expect(toggle.checked).toBe(false);
    expect(screen.getByLabelText('Medir automáticamente al detectar cambios')).toBe(toggle);
    expect(screen.getByText(/Cada medición gasta unos pocos tokens/)).toBeTruthy();

    fireEvent.click(toggle);
    await waitFor(() => expect(api.pty_set_cli_measure_auto).toHaveBeenCalledWith(true));
    await waitFor(() => expect(api.pty_auto_measure_tick).toHaveBeenCalledWith(null));
    expect(api.pty_auto_measure_tick).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId('measure-auto-toggle').checked).toBe(true));
    expect(api.pty_get_cli_measurements.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('auto_toggle_off_does_not_tick', async () => {
    let autoEnabled = true;
    const api = {
      pty_get_cli_measurements: vi.fn(async () => ({ ...snapshot([item()]), auto_enabled: autoEnabled })),
      pty_set_cli_measure_auto: vi.fn(async (enabled) => {
        autoEnabled = enabled;
        return { success: true, auto_enabled: enabled };
      }),
      pty_auto_measure_tick: vi.fn(async () => ({ success: true })),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    const toggle = await screen.findByTestId('measure-auto-toggle');
    expect(toggle.checked).toBe(true);
    fireEvent.click(toggle);
    await waitFor(() => expect(api.pty_set_cli_measure_auto).toHaveBeenCalledWith(false));
    await waitFor(() => expect(screen.getByTestId('measure-auto-toggle').checked).toBe(false));
    expect(api.pty_auto_measure_tick).not.toHaveBeenCalled();
  });

  it('auto_toggle_shows_error_and_keeps_state_when_bridge_refuses', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => ({ ...snapshot([item()]), auto_enabled: false })),
      pty_set_cli_measure_auto: vi.fn(async () => ({ success: false, code: 'SETTINGS_WRITE_FAILED', message: 'No se pudo guardar.' })),
      pty_auto_measure_tick: vi.fn(),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);

    fireEvent.click(await screen.findByTestId('measure-auto-toggle'));
    expect((await screen.findByTestId('measure-feedback')).textContent).toContain('No se pudo cambiar la medición automática');
    expect(screen.getByTestId('measure-auto-toggle').checked).toBe(false);
    expect(api.pty_auto_measure_tick).not.toHaveBeenCalled();
  });

  it('auto_toggle_absent_without_bridge_method', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => snapshot([item()])),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);
    await screen.findByTestId('measure-row-claude');
    expect(screen.queryByTestId('measure-auto-toggle')).toBeNull();
    expect(screen.queryByText('Medir automáticamente al detectar cambios')).toBeNull();
  });

  it('renders_nothing_when_list_method_is_missing_even_with_auto_method', () => {
    const { container } = render(
      <CliMeasurementSettingsSection api={{ pty_set_cli_measure_auto: vi.fn(), pty_auto_measure_tick: vi.fn() }} />
    );
    expect(container.textContent).toBe('');
  });

  it('keeps_polling_while_an_item_is_auto_pending', async () => {
    const api = {
      pty_get_cli_measurements: vi.fn(async () => ({
        ...snapshot([item({ auto_pending: true })]),
        auto_enabled: true
      })),
      pty_set_cli_measure_auto: vi.fn(),
      pty_auto_measure_tick: vi.fn(),
      pty_measure_cli: vi.fn(),
      pty_cancel_cli_measurement: vi.fn(),
      pty_forget_cli_measurement: vi.fn()
    };
    render(<CliMeasurementSettingsSection api={api} />);
    await screen.findByTestId('measure-auto-toggle');
    await waitFor(() => expect(api.pty_get_cli_measurements.mock.calls.length).toBeGreaterThanOrEqual(3), { timeout: 4000 });
    expect(api.pty_auto_measure_tick).not.toHaveBeenCalled();
  });
});
