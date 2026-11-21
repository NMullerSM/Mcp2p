import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAppConfirmation } from '../common/useAppConfirmation';
import { MeasurementDot } from '../common/MeasurementDot';
import { describeRelayError } from '../relay/relayModel';
import { btnBase, mutedText, toneBox } from '../relay/relayStyles';

export const MEASUREMENT_POLL_MS = 1500;

const STATE_LABELS = {
  measured: 'Medido y activo',
  not_measured: 'Sin medir',
  stale: 'Hay que volver a medir',
  failed: 'No se pudo medir',
  unsupported: 'No admitido'
};

// Codigos propios de la medicion: texto de causa y accion. El resto usa describeRelayError.
const MEASURE_ERROR_HELP = {
  MEASUREMENT_BUSY: 'Ya hay otra medición en curso. Espere a que termine o cáncelela.',
  CLI_NOT_INSTALLED: 'Ese CLI no está instalado en este equipo.',
  UNSUPPORTED_CLI: 'Este CLI no admite la medición automática.'
};

const REMEASURE_STATES = ['measured', 'stale', 'failed'];

const rowStyle = {
  display: 'flex',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  flexWrap: 'wrap',
  gap: '8px',
  padding: '6px 0',
  borderBottom: '1px solid var(--border-subtle)'
};

function measureError(res, action) {
  const help = res && typeof res === 'object' ? MEASURE_ERROR_HELP[res.code] : undefined;
  if (help) return `No se pudo ${action}: ${help}`;
  return describeRelayError(res || {}, action);
}

// El contrato admite la confirmacion plana (token en la raiz); useAppConfirmation espera `confirmation`.
function withConfirmation(res) {
  if (res?.code === 'CONFIRMATION_REQUIRED' && !res.confirmation && res.token) {
    return {
      ...res,
      confirmation: {
        token: res.token,
        title: res.title,
        message: res.message,
        rememberable: Boolean(res.rememberable)
      }
    };
  }
  return res;
}

function statusOf(item) {
  if (!item.installed) return 'No instalado';
  if (item.state === 'measured') {
    const details = [item.measured_at, item.cli_version ? `versión ${item.cli_version}` : null].filter(Boolean);
    return details.length > 0 ? `${STATE_LABELS.measured} · ${details.join(' · ')}` : STATE_LABELS.measured;
  }
  if (item.state === 'stale') return `${STATE_LABELS.stale}: el programa cambió desde la última medición.`;
  return STATE_LABELS[item.state] || String(item.state || 'Desconocido');
}

function buttonStyle(disabled) {
  return { ...btnBase, opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer' };
}

function ProgressBlock({ running, onCancel, cancelBusy }) {
  const total = Number(running.steps_total) || 0;
  const done = Number(running.steps_done) || 0;
  return (
    <div data-testid="measure-running" style={{ display: 'flex', flexDirection: 'column', gap: '4px', width: '100%' }}>
      <span style={mutedText}>{running.message || 'Midiendo…'}</span>
      <progress
        data-testid="measure-progress"
        value={total > 0 ? done : undefined}
        max={total > 0 ? total : undefined}
        style={{ width: '100%' }}
      />
      <div>
        <button
          type="button"
          data-testid="measure-cancel"
          disabled={cancelBusy}
          onClick={onCancel}
          style={buttonStyle(cancelBusy)}
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}

/**
 * Medicion automatica de CLIs (WP-5d): una fila por CLI con su estado, boton «Medir» (con confirmacion
 * propia), «Olvidar» y progreso mientras hay una medicion en curso. Sin los metodos del puente no pinta nada.
 */
export function CliMeasurementSettingsSection({ api, projectUuid = null }) {
  const available = typeof api?.pty_get_cli_measurements === 'function'
    && typeof api?.pty_measure_cli === 'function'
    && typeof api?.pty_cancel_cli_measurement === 'function'
    && typeof api?.pty_forget_cli_measurement === 'function';
  const { dialog, run } = useAppConfirmation(api);
  const [snapshot, setSnapshot] = useState({ running: null, items: [], autoEnabled: false });
  const [loaded, setLoaded] = useState(false);
  const [busyKey, setBusyKey] = useState(null);
  const [feedback, setFeedback] = useState(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const autoHelpId = useId();
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (typeof api?.pty_get_cli_measurements !== 'function') return;
    try {
      const res = await api.pty_get_cli_measurements();
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setFeedback({ tone: 'error', text: measureError(res, 'leer la medición de CLIs') });
      } else {
        setSnapshot({
          running: res?.running || null,
          items: Array.isArray(res?.items) ? res.items : [],
          autoEnabled: Boolean(res?.auto_enabled)
        });
      }
    } catch (err) {
      if (mountedRef.current) {
        setFeedback({ tone: 'error', text: measureError(err, 'leer la medición de CLIs') });
      }
    } finally {
      if (mountedRef.current) setLoaded(true);
    }
  }, [api]);

  // Una lectura al montar; el sondeo solo corre mientras hay una medicion en curso.
  useEffect(() => {
    if (!available) return undefined;
    void refresh();
    return undefined;
  }, [available, refresh]);

  const running = snapshot.running;
  const isRunning = running !== null;
  // Con la medición automática puede haber trabajo pendiente sin medición en curso: también hay que seguir leyendo.
  const autoPending = snapshot.items.some((item) => item.auto_pending);
  const polling = isRunning || autoPending;
  useEffect(() => {
    if (!available || !polling) return undefined;
    const timer = setInterval(() => void refresh(), MEASUREMENT_POLL_MS);
    return () => clearInterval(timer);
  }, [available, polling, refresh]);

  if (!available) return null;

  const { items, autoEnabled } = snapshot;
  const autoAvailable = typeof api?.pty_set_cli_measure_auto === 'function';
  const labelOf = (cliId) => items.find((item) => item.cli_id === cliId)?.display_name || cliId;
  const runningInList = isRunning && items.some((item) => item.cli_id === running.cli_id);
  const restartNeeded = items.some((item) => item.restart_needed);

  const measure = async (item) => {
    if (busyKey || isRunning) return;
    setBusyKey(`measure:${item.cli_id}`);
    setFeedback(null);
    try {
      const invokeMeasure = () => (
        projectUuid ? api.pty_measure_cli(item.cli_id, projectUuid) : api.pty_measure_cli(item.cli_id)
      );
      const res = await run(() => Promise.resolve(invokeMeasure()).then(withConfirmation));
      if (!mountedRef.current) return;
      if (res?.success === false) {
        // Rechazar el dialogo no es un error: no se muestra mensaje.
        if (res.code !== 'CONFIRMATION_DECLINED') {
          setFeedback({ tone: 'error', text: measureError(res, `medir ${labelOf(item.cli_id)}`) });
        }
      } else {
        setFeedback({ tone: 'info', text: `Medición de ${labelOf(item.cli_id)} iniciada. Puede tardar unos minutos.` });
      }
    } catch (err) {
      if (mountedRef.current) {
        setFeedback({ tone: 'error', text: measureError(err, `medir ${labelOf(item.cli_id)}`) });
      }
    } finally {
      if (mountedRef.current) setBusyKey(null);
    }
    await refresh();
  };

  const forget = async (item) => {
    if (busyKey || isRunning) return;
    setBusyKey(`forget:${item.cli_id}`);
    setFeedback(null);
    try {
      const res = await api.pty_forget_cli_measurement(item.cli_id);
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setFeedback({ tone: 'error', text: measureError(res, `olvidar la medición de ${labelOf(item.cli_id)}`) });
      } else {
        setFeedback({
          tone: 'ok',
          text: `Medición de ${labelOf(item.cli_id)} olvidada. Sus mensajes vuelven a llegar solo a la bandeja.`
        });
      }
    } catch (err) {
      if (mountedRef.current) {
        setFeedback({ tone: 'error', text: measureError(err, `olvidar la medición de ${labelOf(item.cli_id)}`) });
      }
    } finally {
      if (mountedRef.current) setBusyKey(null);
    }
    await refresh();
  };

  const cancel = async () => {
    if (busyKey === 'cancel') return;
    setBusyKey('cancel');
    setFeedback(null);
    try {
      const res = await api.pty_cancel_cli_measurement();
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setFeedback({ tone: 'error', text: measureError(res, 'cancelar la medición') });
      } else {
        setFeedback({ tone: 'info', text: 'Cancelación solicitada.' });
      }
    } catch (err) {
      if (mountedRef.current) setFeedback({ tone: 'error', text: measureError(err, 'cancelar la medición') });
    } finally {
      if (mountedRef.current) setBusyKey(null);
    }
    await refresh();
  };

  const toggleAuto = async (enabled) => {
    if (autoBusy) return;
    setAutoBusy(true);
    setFeedback(null);
    try {
      const res = await api.pty_set_cli_measure_auto(enabled);
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setFeedback({ tone: 'error', text: measureError(res, 'cambiar la medición automática') });
      } else if (enabled && typeof api.pty_auto_measure_tick === 'function') {
        // Al activarlo, los CLIs que ya cambiaron se miden ahora. Un fallo aquí no invalida el ajuste guardado.
        try {
          await api.pty_auto_measure_tick(null);
        } catch {
          // La siguiente lectura del snapshot muestra lo que quedó pendiente.
        }
      }
    } catch (err) {
      if (mountedRef.current) {
        setFeedback({ tone: 'error', text: measureError(err, 'cambiar la medición automática') });
      }
    } finally {
      if (mountedRef.current) setAutoBusy(false);
    }
    await refresh();
  };

  const cancelBusy = busyKey === 'cancel';
  const cancelBlock = (
    <ProgressBlock running={running || {}} onCancel={cancel} cancelBusy={cancelBusy} />
  );

  return (
    <div data-testid="cli-measurement-settings" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {loaded && autoAvailable && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', paddingBottom: '8px', borderBottom: '1px solid var(--border-subtle)' }}>
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '12px',
              fontWeight: 600,
              color: 'var(--text-primary)',
              cursor: autoBusy ? 'wait' : 'pointer'
            }}
          >
            <input
              type="checkbox"
              data-testid="measure-auto-toggle"
              checked={autoEnabled}
              disabled={autoBusy}
              aria-describedby={autoHelpId}
              onChange={(event) => void toggleAuto(event.target.checked)}
            />
            <span>Medir automáticamente al detectar cambios</span>
          </label>
          <span id={autoHelpId} style={mutedText}>
            Cuando un CLI cambia (se actualiza o se reinstala), MCP2P lo vuelve a medir solo en segundo plano. Cada medición gasta unos pocos tokens.
          </span>
        </div>
      )}
      {!loaded && <span style={mutedText}>Cargando…</span>}
      {loaded && items.length === 0 && (
        <span data-testid="measure-empty" style={mutedText}>No hay CLIs que medir.</span>
      )}
      {items.map((item) => {
        const isRunningRow = isRunning && running.cli_id === item.cli_id;
        const remeasure = REMEASURE_STATES.includes(item.state);
        const measureDisabled = Boolean(busyKey) || isRunning || !item.installed || item.state === 'unsupported';
        const forgetVisible = item.state === 'measured' || item.state === 'stale';
        const forgetDisabled = Boolean(busyKey) || isRunning;
        return (
          <div key={item.cli_id} data-testid={`measure-row-${item.cli_id}`} style={rowStyle}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0, flex: '1 1 200px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
                {item.dot && <MeasurementDot color={item.dot} label={item.dot_label || statusOf(item)} />}
                <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  {item.display_name || item.cli_id}
                </span>
              </div>
              <span data-testid={`measure-status-${item.cli_id}`} style={mutedText}>{statusOf(item)}</span>
              {item.state === 'failed' && item.reason_text && (
                <span data-testid={`measure-reason-${item.cli_id}`} style={{ ...mutedText, color: 'var(--accent-danger)' }}>
                  {item.reason_text}
                </span>
              )}
              {isRunningRow && cancelBlock}
            </div>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
              <button
                type="button"
                data-testid={`measure-${item.cli_id}`}
                disabled={measureDisabled}
                onClick={() => void measure(item)}
                style={buttonStyle(measureDisabled)}
              >
                {remeasure ? 'Volver a medir' : 'Medir'}
              </button>
              {forgetVisible && (
                <button
                  type="button"
                  data-testid={`forget-${item.cli_id}`}
                  disabled={forgetDisabled}
                  onClick={() => void forget(item)}
                  style={buttonStyle(forgetDisabled)}
                >
                  Olvidar
                </button>
              )}
            </div>
          </div>
        );
      })}
      {isRunning && !runningInList && (
        <div style={rowStyle}>{cancelBlock}</div>
      )}
      {restartNeeded && (
        <div data-testid="measure-restart-note" role="status" style={toneBox('warning')}>
          Cierra y vuelve a abrir las terminales de este CLI para que usen la entrega directa.
        </div>
      )}
      {feedback && (
        <div data-testid="measure-feedback" role="status" style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}
      {typeof document !== 'undefined' ? createPortal(dialog, document.body) : null}
    </div>
  );
}
