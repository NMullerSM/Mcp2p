import React, { useEffect, useRef, useState } from 'react';
import { fieldStyle, mutedText, toneBox } from '../relay/relayStyles';
import { describeDeliveryPlatform } from '../terminals/deliveryModel';

const DEFAULT_OPTIONS = [
  { value: 'direct', label: 'Entrega directa por defecto' },
  { value: 'inbox', label: 'Solo bandeja por defecto' }
];

const cellStyle = { padding: '5px 6px', borderBottom: '1px solid var(--border-subtle)', textAlign: 'left', verticalAlign: 'top' };

/** Ajustes de entrega directa: modo por defecto y CLI x sistema medidos. Sin metodos del puente no pinta nada. */
export function DeliverySettingsSection({ api }) {
  const available = typeof api?.relay_get_delivery_overview === 'function'
    && typeof api?.relay_set_delivery_default === 'function';
  const [defaultMode, setDefaultMode] = useState('inbox');
  const [verified, setVerified] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!available) return;
    (async () => {
      try {
        const res = await api.relay_get_delivery_overview();
        if (!mountedRef.current) return;
        if (res?.success === false) {
          setFeedback({ tone: 'error', text: res.message || 'No se pudo leer la entrega directa.' });
        } else {
          if (res?.default_mode) setDefaultMode(res.default_mode === 'direct' ? 'direct' : 'inbox');
          setVerified(Array.isArray(res?.verified) ? res.verified : []);
        }
        setLoaded(true);
      } catch (err) {
        if (mountedRef.current) {
          setFeedback({ tone: 'error', text: `No se pudo leer la entrega directa: ${err?.message || 'error desconocido'}.` });
          setLoaded(true);
        }
      }
    })();
  }, [api, available]);

  if (!available) return null;

  const change = async (next) => {
    const previous = defaultMode;
    setDefaultMode(next);
    setSaving(true);
    setFeedback(null);
    try {
      const res = await api.relay_set_delivery_default(next);
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setDefaultMode(previous);
        setFeedback({ tone: 'error', text: res.message || 'No se pudo guardar la preferencia.' });
      } else {
        setFeedback({ tone: 'ok', text: 'Preferencia guardada.' });
      }
    } catch (err) {
      if (mountedRef.current) {
        setDefaultMode(previous);
        setFeedback({ tone: 'error', text: `No se pudo guardar la preferencia: ${err?.message || 'error desconocido'}.` });
      }
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  };

  return (
    <div data-testid="delivery-settings" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '11.5px', fontWeight: 600 }}>
        Modo por defecto de las terminales
        <select
          data-testid="delivery-default-select"
          value={defaultMode}
          disabled={!loaded || saving}
          onChange={(e) => void change(e.target.value)}
          style={fieldStyle}
        >
          {DEFAULT_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </label>
      <span style={mutedText}>
        La entrega directa solo se usa con CLIs medidos y nunca en terminales con permisos saltados.
      </span>
      {feedback && (
        <div data-testid="delivery-settings-feedback" role="status" style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <span style={{ fontSize: '11.5px', fontWeight: 600 }}>CLI x sistema: entrega directa verificada / no</span>
        {loaded && verified.length === 0 && (
          <span data-testid="delivery-verified-empty" style={mutedText}>Aún no hay CLIs medidos.</span>
        )}
        {verified.length > 0 && (
          <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
            <table data-testid="delivery-verified-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11.5px' }}>
              <thead>
                <tr>
                  <th style={cellStyle}>CLI</th>
                  <th style={cellStyle}>Sistema</th>
                  <th style={cellStyle}>Entrega directa</th>
                </tr>
              </thead>
              <tbody>
                {verified.map((row, index) => (
                  <tr key={`${row.cli_id}-${row.platform}-${index}`} data-testid={`delivery-verified-row-${row.cli_id}`}>
                    <td style={cellStyle}>{row.display_name || row.cli_id}</td>
                    <td style={cellStyle}>{describeDeliveryPlatform(row.platform)}</td>
                    <td style={cellStyle}>{row.verified ? 'Verificada' : 'No verificada'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
