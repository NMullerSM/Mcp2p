import React, { useState } from 'react';
import { Lock } from 'lucide-react';
import { ModalOverlay } from './Modals';

/**
 * Diálogo pequeño que pide la frase de la clave de firma humana (Gate 0c / EGM-53).
 *
 * Es presentacional: la frase vive SOLO en el estado local de este componente y se vacía
 * en cuanto termina `onSubmit` (éxito o error). Nunca se sube a props, contexto, almacenamiento
 * del navegador ni logs. `onSubmit(frase)` devuelve `{ ok, error? }`; con `ok === false` el
 * diálogo se queda abierto mostrando el error (p. ej. BAD_PASSPHRASE).
 */
export function UnlockSigningKeyDialog({ isOpen, onSubmit, onCancel }) {
  const [passphrase, setPassphrase] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (busy || !passphrase) return;
    setBusy(true);
    setError('');
    try {
      const result = await onSubmit(passphrase);
      if (result && result.ok === false) {
        setError(result.error || 'No se pudo desbloquear la clave.');
      }
    } catch (err) {
      setError('No se pudo desbloquear la clave.');
    } finally {
      // La frase se descarta del estado local pase lo que pase.
      setPassphrase('');
      setBusy(false);
    }
  };

  return (
    <ModalOverlay isOpen={isOpen} onClose={busy ? undefined : onCancel} title="Desbloquear clave de firma" maxWidth="400px">
      <form
        onSubmit={handleSubmit}
        data-testid="unlock-signing-key-dialog"
        style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}
      >
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>
          Esta acción se firma con tu clave humana, que está cifrada y bloqueada. Escribe tu frase
          para continuar; la acción se reintentará automáticamente.
        </p>
        <input
          type="password"
          autoComplete="off"
          autoFocus
          spellCheck={false}
          aria-label="Frase de la clave de firma"
          placeholder="Frase de la clave"
          value={passphrase}
          onChange={(e) => setPassphrase(e.target.value)}
          disabled={busy}
          style={{
            padding: '9px 12px',
            fontSize: '13px',
            borderRadius: 'var(--radius-sm)',
            border: `1px solid ${error ? 'var(--accent-danger)' : 'var(--border-subtle)'}`,
            backgroundColor: 'var(--bg-subtle)',
            color: 'var(--text-primary)',
            outline: 'none'
          }}
        />
        {error && (
          <div role="alert" style={{ fontSize: '12px', color: 'var(--accent-danger)' }}>
            {error}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            style={{
              padding: '8px 14px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'transparent',
              color: 'var(--text-secondary)',
              cursor: busy ? 'default' : 'pointer'
            }}
          >
            Cancelar
          </button>
          <button
            type="submit"
            disabled={busy || !passphrase}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '8px 14px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#fff',
              cursor: busy || !passphrase ? 'default' : 'pointer',
              opacity: busy || !passphrase ? 0.6 : 1
            }}
          >
            <Lock size={13} /> {busy ? 'Desbloqueando…' : 'Desbloquear y continuar'}
          </button>
        </div>
      </form>
    </ModalOverlay>
  );
}
