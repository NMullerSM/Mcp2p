// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';
import { TerminalSliderControls } from './TerminalSliderControls';
import { useDeliveryActions } from './useDeliveryActions';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makePane(over = {}) {
  return {
    paneId: 'pane_1',
    open: true,
    alive: true,
    paused: false,
    kind: 'cli',
    cliProfile: 'claude',
    role: 'peer',
    cwd: null,
    cols: 80,
    rows: 24,
    pid: 4242,
    rssMb: 12,
    nodeUuid: 'pane_1_abcd1234',
    registryStatus: 'active',
    exitCode: null,
    autoRelayEnabled: false,
    workerMode: false,
    deliveryMode: 'auto',
    ...over
  };
}

function baseProps(over = {}) {
  return {
    api: {},
    status: { hubRunning: false, hubUrl: null, degraded: false, platformUnsupported: false, manualCommand: null, mcpConfig: null, loading: false },
    catalog: { loaded: true, loading: false, error: null, clis: [], platformSupported: true, warnings: [], hardMaxPanes: 6 },
    maxPanes: 4,
    activePanes: [makePane()],
    focusedPaneId: 'pane_1',
    paneLabels: { pane_1: 'Claude 1' },
    catalogById: {},
    notice: null,
    statusNotice: null,
    cwdValue: '',
    onCwdChange: () => {},
    onRefresh: () => {},
    onLaunch: () => {},
    onFocusPane: () => {},
    onPausePane: () => {},
    onClosePane: () => {},
    onSetMaxPanes: () => {},
    onDismissNotice: () => {},
    onNoticeAction: () => {},
    onOpenSettings: () => {},
    ...over
  };
}

// Envoltorio que cablea el hook real, como hace SwarmTerminalMatrix, para comprobar la llamada al puente.
function Wired({ api, pane, status, onChanged }) {
  const { setMode, busy, error, dialog } = useDeliveryActions({ api, projectUuid: 'proj_1', onChanged });
  return React.createElement(
    'div',
    null,
    React.createElement(TerminalSliderControls, baseProps({
      api,
      activePanes: [pane],
      deliveryStatusByPane: status ? { pane_1: status } : {},
      onSetDeliveryMode: (paneId, mode) => void setMode(paneId, mode),
      deliveryBusy: busy,
      deliveryError: error
    })),
    dialog
  );
}

describe('TerminalSliderControls: entrega directa', () => {
  it('delivery_toggle_cycles_and_calls_api', async () => {
    const api = {
      pty_set_delivery_mode: vi.fn(async (paneId, mode) => ({ success: true, pane_id: paneId, delivery_mode: mode }))
    };
    const onChanged = vi.fn();
    const { rerender } = render(React.createElement(Wired, { api, pane: makePane({ deliveryMode: 'auto' }), onChanged }));

    const toggle = screen.getByTestId('pane-delivery-toggle-pane_1');
    expect(toggle.textContent).toContain('Entrega directa: AUTO');
    fireEvent.click(toggle);
    await vi.waitFor(() => expect(api.pty_set_delivery_mode).toHaveBeenCalledWith('pane_1', 'direct'));
    // Mientras la llamada va en vuelo el boton queda deshabilitado (no se encadenan clics).
    await vi.waitFor(() => expect(screen.getByTestId('pane-delivery-toggle-pane_1').disabled).toBe(false));

    rerender(React.createElement(Wired, { api, pane: makePane({ deliveryMode: 'direct' }), onChanged }));
    expect(screen.getByTestId('pane-delivery-toggle-pane_1').textContent).toContain('Entrega directa: ON');
    fireEvent.click(screen.getByTestId('pane-delivery-toggle-pane_1'));
    await vi.waitFor(() => expect(api.pty_set_delivery_mode).toHaveBeenLastCalledWith('pane_1', 'inbox'));
    await vi.waitFor(() => expect(screen.getByTestId('pane-delivery-toggle-pane_1').disabled).toBe(false));

    rerender(React.createElement(Wired, { api, pane: makePane({ deliveryMode: 'inbox' }), onChanged }));
    expect(screen.getByTestId('pane-delivery-toggle-pane_1').textContent).toContain('Entrega directa: OFF');
    fireEvent.click(screen.getByTestId('pane-delivery-toggle-pane_1'));
    await vi.waitFor(() => expect(api.pty_set_delivery_mode).toHaveBeenLastCalledWith('pane_1', 'auto'));
    expect(api.pty_set_delivery_mode).toHaveBeenCalledTimes(3);
  });

  it('delivery_reason_shown_when_effective_inbox', () => {
    const { rerender } = render(React.createElement(TerminalSliderControls, baseProps({
      api: {},
      deliveryStatusByPane: { pane_1: { pane_id: 'pane_1', effective_mode: 'inbox', mode_reason: 'CLI_NOT_VERIFIED', in_flight: false, queued: 0 } },
      onSetDeliveryMode: () => {}
    })));
    expect(screen.getByTestId('pane-delivery-reason-pane_1').textContent).toBe('Este CLI aun no esta medido: solo bandeja.');

    // Codigo desconocido: no se muestra nada crudo.
    rerender(React.createElement(TerminalSliderControls, baseProps({
      api: {},
      deliveryStatusByPane: { pane_1: { pane_id: 'pane_1', effective_mode: 'inbox', mode_reason: 'CODIGO_RARO' } },
      onSetDeliveryMode: () => {}
    })));
    expect(screen.queryByTestId('pane-delivery-reason-pane_1')).toBeNull();

    // Entrega efectiva directa: sin motivo.
    rerender(React.createElement(TerminalSliderControls, baseProps({
      api: {},
      deliveryStatusByPane: { pane_1: { pane_id: 'pane_1', effective_mode: 'direct', mode_reason: null } },
      onSetDeliveryMode: () => {}
    })));
    expect(screen.queryByTestId('pane-delivery-reason-pane_1')).toBeNull();
  });

  it('delivery_info_hint_present', () => {
    const onOpenSettings = vi.fn();
    render(React.createElement(TerminalSliderControls, baseProps({
      api: {},
      onOpenSettings,
      onSetDeliveryMode: () => {}
    })));
    const row = screen.getByTestId('active-row-pane_1');
    expect(within(row).getByTestId('pane-delivery-hint-pane_1')).toBeTruthy();
  });

  it('no_toggle_without_backend_method', () => {
    render(React.createElement(TerminalSliderControls, baseProps({ api: {} })));
    expect(screen.queryByTestId('pane-delivery-toggle-pane_1')).toBeNull();
  });
});
