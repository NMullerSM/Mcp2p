import React, { useState, useEffect, useCallback, useRef } from 'react';
import { KeyRound, Lock, Unlock } from 'lucide-react';

// Debe coincidir con MIN_PASSPHRASE_LENGTH de src/p2p/crypto.py.
const MIN_PASSPHRASE_LENGTH = 10;
const STATUS_REFRESH_MS = 30000;

function shortKey(key) {
  if (!key || key.length <= 20) return key || '';
  return `${key.slice(0, 8)}…${key.slice(-8)}`;
}

const inputStyle = {
  padding: '8px 10px',
  fontSize: '12px',
  borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--bg-subtle)',
  color: 'var(--text-primary)',
  outline: 'none'
};

const buttonStyle = (primary, disabled) => ({
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '6px',
  padding: '7px 12px',
  fontSize: '12px',
  fontWeight: 600,
  borderRadius: 'var(--radius-sm)',
  border: primary ? 'none' : '1px solid var(--border-subtle)',
  backgroundColor: primary ? 'var(--accent-primary)' : 'var(--bg-subtle)',
  color: primary ? '#fff' : 'var(--text-primary)',
  cursor: disabled ? 'default' : 'pointer',
  opacity: disabled ? 0.6 : 1
});

/**
 * Sección "Firma humana" de Configuración (Gate 0c / EGM-53).
 *
 * Muestra el estado real de la clave Ed25519 humana (`get_signing_key_status`):
 *  - sin cifrar            -> permite fijar una frase (cifra y migra la clave);
 *  - cifrada y bloqueada   -> permite desbloquear;
 *  - desbloqueada          -> permite bloquear ahora o cambiar la frase.
 * Todas las frases viven solo en estado local y se vacían al terminar cada llamada.
 */
export function SigningKeySection({ api }) {
  const [status, setStatus] = useState(null);
  const [mode, setMode] = useState(null); // null | 'set' | 'unlock'
  const [pass, setPass] = useState('');
  const [pass2, setPass2] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null); // { type: 'error' | 'success', text }
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const refresh = useCallback(async () => {
    if (!api || typeof api.get_signing_key_status !== 'function') return;
    try {
      const res = await api.get_signing_key_status();
      if (mountedRef.current && res && res.success) setStatus(res);
    } catch {
      /* el estado se queda como estaba */
    }
  }, [api]);

  useEffect(() => {
    refresh();
    // El desbloqueo caduca solo en el backend (15 min): se relee para no mostrar un estado viejo.
    const id = setInterval(refresh, STATUS_REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const clearSecrets = () => {
    setPass('');
    setPass2('');
  };

  const closeForm = () => {
    clearSecrets();
    setMode(null);
  };

  // Ejecuta una llamada que lleva la frase; la frase se descarta siempre al terminar.
  const runWithPassphrase = async (call, successText) => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await call(pass);
      if (!mountedRef.current) return;
      if (res && res.success) {
        setStatus(res);
        setMode(null);
        setMessage({ type: 'success', text: successText });
      } else {
        setMessage({ type: 'error', text: (res && res.error) || 'La operación falló.' });
      }
    } catch {
      if (mountedRef.current) setMessage({ type: 'error', text: 'Error de comunicación con la aplicación.' });
    } finally {
      if (mountedRef.current) {
        clearSecrets();
        setBusy(false);
      }
    }
  };

  const handleSetPassphrase = (e) => {
    e.preventDefault();
    if (busy) return;
    if (pass.length < MIN_PASSPHRASE_LENGTH) {
      setMessage({ type: 'error', text: `La frase debe tener al menos ${MIN_PASSPHRASE_LENGTH} caracteres.` });
      return;
    }
    if (pass !== pass2) {
      setMessage({ type: 'error', text: 'Las dos frases no coinciden.' });
      return;
    }
    runWithPassphrase((p) => api.set_signing_key_passphrase(p), 'Frase fijada. La clave está cifrada y desbloqueada.');
  };

  const handleUnlock = (e) => {
    e.preventDefault();
    if (busy || !pass) return;
    runWithPassphrase((p) => api.unlock_signing_key(p), 'Clave desbloqueada.');
  };

  const handleLock = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await api.lock_signing_key();
      if (!mountedRef.current) return;
      if (res && res.success) {
        setStatus(res);
        setMode(null);
      } else {
        setMessage({ type: 'error', text: (res && res.error) || 'No se pudo bloquear la clave.' });
      }
    } catch {
      if (mountedRef.current) setMessage({ type: 'error', text: 'Error de comunicación con la aplicación.' });
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  // Sin puente (dev sin backend) o sin respuesta todavía: la sección no aparece.
  if (!status) return null;

  const stateKey = !status.encrypted ? 'plain' : status.locked ? 'locked' : 'unlocked';
  const STATE_LABEL = {
    plain: 'Sin cifrar',
    locked: 'Cifrada y bloqueada',
    unlocked: 'Desbloqueada'
  };
  const stateColor = stateKey === 'unlocked' ? 'var(--accent-success)'
    : stateKey === 'locked' ? 'var(--accent-warning)' : 'var(--text-muted)';

  const renderSetForm = () => (
    <form onSubmit={handleSetPassphrase} style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        aria-label="Frase nueva"
        placeholder={`Frase (mínimo ${MIN_PASSPHRASE_LENGTH} caracteres)`}
        value={pass}
        onChange={(e) => setPass(e.target.value)}
        disabled={busy}
        style={inputStyle}
      />
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        aria-label="Repite la frase"
        placeholder="Repite la frase"
        value={pass2}
        onChange={(e) => setPass2(e.target.value)}
        disabled={busy}
        style={inputStyle}
      />
      <div
        data-testid="signing-key-no-recovery-warning"
        style={{ fontSize: '11px', color: 'var(--accent-warning)', lineHeight: 1.4 }}
      >
        Si olvidas esta frase no hay recuperación: la clave cifrada no se puede descifrar ni restaurar
        desde la aplicación, y perderías la capacidad de firmar con esta identidad.
      </div>
      <div style={{ display: 'flex', gap: '8px' }}>
        <button type="submit" disabled={busy || !pass || !pass2} style={buttonStyle(true, busy || !pass || !pass2)}>
          <Lock size={13} /> {stateKey === 'plain' ? 'Cifrar clave' : 'Cambiar frase'}
        </button>
        <button type="button" onClick={closeForm} disabled={busy} style={buttonStyle(false, busy)}>
          Cancelar
        </button>
      </div>
    </form>
  );

  const renderUnlockForm = () => (
    <form onSubmit={handleUnlock} style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        aria-label="Frase de la clave"
        placeholder="Frase de la clave"
        value={pass}
        onChange={(e) => setPass(e.target.value)}
        disabled={busy}
        style={inputStyle}
      />
      <div style={{ display: 'flex', gap: '8px' }}>
        <button type="submit" disabled={busy || !pass} style={buttonStyle(true, busy || !pass)}>
          <Unlock size={13} /> Desbloquear
        </button>
        <button type="button" onClick={closeForm} disabled={busy} style={buttonStyle(false, busy)}>
          Cancelar
        </button>
      </div>
    </form>
  );

  return (
    <div
      data-testid="signing-key-section"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        padding: '14px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <h4 style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
          <KeyRound size={14} color="var(--accent-primary)" /> Firma humana
        </h4>
        <span
          data-testid="signing-key-state"
          style={{ fontSize: '11px', fontWeight: 600, color: stateColor }}
        >
          {STATE_LABEL[stateKey]}
        </span>
      </div>

      {status.public_key && (
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }} title={status.public_key}>
          Clave pública: {shortKey(status.public_key)}
        </div>
      )}

      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.4 }}>
        {stateKey === 'plain' && 'La clave que firma tus aprobaciones está en disco sin cifrar. Es opcional protegerla con una frase que solo tú teclees.'}
        {stateKey === 'locked' && 'Desbloquéala para firmar aprobaciones; se vuelve a bloquear sola a los 15 minutos.'}
        {stateKey === 'unlocked' && 'Puede firmar ahora; se vuelve a bloquear sola a los 15 minutos o al pulsar Bloquear.'}
      </p>

      {message && (
        <div
          role={message.type === 'error' ? 'alert' : 'status'}
          style={{ fontSize: '12px', color: message.type === 'error' ? 'var(--accent-danger)' : 'var(--accent-success)' }}
        >
          {message.text}
        </div>
      )}

      {mode === 'set' && renderSetForm()}
      {mode === 'unlock' && renderUnlockForm()}

      {!mode && (
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {stateKey === 'plain' && (
            <button type="button" onClick={() => { setMessage(null); setMode('set'); }} style={buttonStyle(true, false)}>
              <Lock size={13} /> Fijar frase
            </button>
          )}
          {stateKey === 'locked' && (
            <button type="button" onClick={() => { setMessage(null); setMode('unlock'); }} style={buttonStyle(true, false)}>
              <Unlock size={13} /> Desbloquear
            </button>
          )}
          {stateKey === 'unlocked' && (
            <>
              <button type="button" onClick={handleLock} disabled={busy} style={buttonStyle(true, busy)}>
                <Lock size={13} /> Bloquear ahora
              </button>
              <button type="button" onClick={() => { setMessage(null); setMode('set'); }} disabled={busy} style={buttonStyle(false, busy)}>
                Cambiar frase
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
