import React, { useEffect, useRef, useState } from 'react';
import { fieldStyle, mutedText, toneBox } from '../relay/relayStyles';

const POLICIES = [
  { value: 'ask', label: 'Preguntarme al abrir el proyecto', hint: 'Muestra un aviso con los agentes de tu última sesión.' },
  { value: 'auto', label: 'Restaurar automáticamente', hint: 'Relanza los agentes de la última sesión al abrir el proyecto.' },
  { value: 'never', label: 'No restaurar nunca por mi cuenta', hint: 'Solo restauras a mano desde el botón de sesión anterior.' }
];

/** Preferencia global de restauración de sesiones (`ask` | `auto` | `never`). */
export function SessionRestoreSettingsSection({ api }) {
  const [policy, setPolicy] = useState('ask');
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
    if (typeof api?.pty_get_restore_policy !== 'function') return;
    (async () => {
      try {
        const res = await api.pty_get_restore_policy();
        if (!mountedRef.current) return;
        if (res?.success !== false && typeof res?.policy === 'string') setPolicy(res.policy);
        setLoaded(true);
      } catch {
        if (mountedRef.current) setLoaded(true);
      }
    })();
  }, [api]);

  if (typeof api?.pty_set_restore_policy !== 'function') return null;

  const change = async (next) => {
    const previous = policy;
    setPolicy(next);
    setSaving(true);
    setFeedback(null);
    try {
      const res = await api.pty_set_restore_policy(next);
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setPolicy(previous);
        setFeedback({ tone: 'error', text: res.message || 'No se pudo guardar la preferencia.' });
      } else {
        setFeedback({ tone: 'ok', text: 'Preferencia guardada.' });
      }
    } catch (err) {
      if (mountedRef.current) {
        setPolicy(previous);
        setFeedback({ tone: 'error', text: `No se pudo guardar la preferencia: ${err?.message || 'error desconocido'}.` });
      }
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  };

  const current = POLICIES.find((p) => p.value === policy) || POLICIES[0];
  return (
    <div data-testid="session-restore-settings" style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '11.5px', fontWeight: 600 }}>
        Al abrir un proyecto
        <select
          data-testid="session-restore-policy"
          value={policy}
          disabled={!loaded || saving}
          onChange={(e) => void change(e.target.value)}
          style={fieldStyle}
        >
          {POLICIES.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </select>
      </label>
      <span style={mutedText}>{current.hint}</span>
      {feedback && (
        <div data-testid="session-restore-feedback" role="status" style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}
    </div>
  );
}
