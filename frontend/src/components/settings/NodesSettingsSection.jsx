import React, { useCallback, useEffect, useState } from 'react';
import { RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-react';
import { describeRelayError } from '../relay/relayModel';
import { POLICY_OPTIONS, relationLabel, shortFingerprint } from '../relay/nodeModel';
import { btnBase, chip, fieldStyle, mutedText, toneBox } from '../relay/relayStyles';

/**
 * Sección «Otros equipos» dentro de Ajustes -> Terminales y relevo (v2.0.2, WP-7).
 * Permite editar el nombre local del equipo y revisar/cambiar la política de cada nodo remoto conocido.
 */
export function NodesSettingsSection({ api }) {
  const [localNode, setLocalNode] = useState(null);
  const [localNameDraft, setLocalNameDraft] = useState('');
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [savingName, setSavingName] = useState(false);
  const [busyFp, setBusyFp] = useState(null);
  const [feedback, setFeedback] = useState(null);
  const [error, setError] = useState(null);

  const loadAll = useCallback(async () => {
    if (!api?.relay_list_nodes && !api?.relay_get_local_node) return;
    setLoading(true);
    setError(null);
    try {
      const [localRes, nodesRes] = await Promise.all([
        api?.relay_get_local_node ? api.relay_get_local_node() : Promise.resolve(null),
        api?.relay_list_nodes ? api.relay_list_nodes() : Promise.resolve(null)
      ]);
      if (localRes?.success) {
        setLocalNode(localRes);
        setLocalNameDraft(localRes.display_name || '');
      }
      if (nodesRes) {
        if (nodesRes.success === false) {
          setError(describeRelayError(nodesRes, 'cargar la lista de nodos'));
        } else {
          setNodes(Array.isArray(nodesRes.nodes) ? nodesRes.nodes : []);
        }
      }
    } catch (err) {
      setError(describeRelayError(err, 'cargar los nodos'));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleSaveLocalName = async () => {
    if (!api?.relay_set_local_node_name || savingName) return;
    setSavingName(true);
    setFeedback(null);
    try {
      const res = await api.relay_set_local_node_name(localNameDraft);
      if (res?.success === false) {
        setFeedback({ tone: 'error', text: describeRelayError(res, 'guardar el nombre del equipo') });
      } else {
        setLocalNode((prev) => ({ ...(prev || {}), display_name: res.display_name }));
        setLocalNameDraft(res.display_name || '');
        setFeedback({ tone: 'ok', text: 'Nombre del equipo guardado.' });
      }
    } catch (err) {
      setFeedback({ tone: 'error', text: describeRelayError(err, 'guardar el nombre del equipo') });
    } finally {
      setSavingName(false);
    }
  };

  const handlePolicyChange = async (deviceFingerprint, nextPolicy) => {
    if (!api?.relay_set_node_policy || busyFp) return;
    setBusyFp(deviceFingerprint);
    setFeedback(null);
    try {
      const res = await api.relay_set_node_policy(deviceFingerprint, nextPolicy);
      if (res?.success === false) {
        setFeedback({
          tone: res.code === 'CONFIRMATION_DECLINED' ? 'info' : 'error',
          text: describeRelayError(res, 'cambiar la política del nodo')
        });
      } else {
        setFeedback({ tone: 'ok', text: 'Política del nodo actualizada.' });
        await loadAll();
      }
    } catch (err) {
      setFeedback({ tone: 'error', text: describeRelayError(err, 'cambiar la política del nodo') });
    } finally {
      setBusyFp(null);
    }
  };

  if (!api?.relay_list_nodes && !api?.relay_get_local_node) {
    return null;
  }

  return (
    <div
      data-testid="nodes-settings-section"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        marginTop: '6px',
        maxWidth: '100%',
        boxSizing: 'border-box'
      }}
    >
      {feedback && (
        <div data-testid="nodes-settings-feedback" role="status" style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}

      {error && (
        <div data-testid="nodes-settings-error" role="alert" style={toneBox('error')}>
          {error}
        </div>
      )}

      {localNode && (
        <div
          data-testid="nodes-local-section"
          style={{
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-sm)',
            padding: '8px 10px',
            backgroundColor: 'var(--bg-app)',
            display: 'flex',
            flexDirection: 'column',
            gap: '6px'
          }}
        >
          <div style={{ fontSize: '11.5px', fontWeight: 600, color: 'var(--text-primary)' }}>
            Este equipo
          </div>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
            <label htmlFor="nodes-local-name-input" style={{ ...mutedText, fontSize: '11px' }}>
              Nombre visible:
            </label>
            <input
              id="nodes-local-name-input"
              data-testid="nodes-local-name-input"
              type="text"
              maxLength={40}
              value={localNameDraft}
              onChange={(e) => setLocalNameDraft(e.target.value)}
              style={{ ...fieldStyle, flex: '1 1 160px', minWidth: 0 }}
            />
            <button
              type="button"
              data-testid="nodes-local-name-save"
              disabled={savingName}
              onClick={handleSaveLocalName}
              style={{ ...btnBase, color: 'var(--accent-primary)' }}
            >
              Guardar
            </button>
          </div>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
            {(localNode.fingerprint_short || localNode.fingerprint) && (
              <span data-testid="nodes-local-fingerprint" style={chip} title={localNode.fingerprint || ''}>
                Huella: {localNode.fingerprint_short || shortFingerprint(localNode.fingerprint)}
              </span>
            )}
            <span style={chip}>
              {localNode.human_key_available ? 'Clave humana desbloqueada' : 'Clave humana bloqueada o ausente'}
            </span>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '11.5px', fontWeight: 600, color: 'var(--text-primary)' }}>
          Nodos remotos conocidos ({nodes.length})
        </span>
        <button
          type="button"
          data-testid="nodes-refresh"
          aria-label="Actualizar nodos remotos"
          disabled={loading}
          onClick={loadAll}
          style={{ ...btnBase, marginLeft: 'auto', padding: '3px 7px' }}
        >
          <RefreshCw size={11} />
        </button>
      </div>

      {nodes.length === 0 ? (
        <p data-testid="nodes-remote-empty" style={{ ...mutedText, margin: 0 }}>
          Todavía no ha contactado ningún nodo remoto.
        </p>
      ) : (
        <ul
          data-testid="nodes-remote-list"
          style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}
        >
          {nodes.map((node) => {
            const fp = node.device_fingerprint;
            const fpShort = node.fingerprint_short || shortFingerprint(fp);
            const isSameOwner = node.relation === 'same_owner';
            return (
              <li
                key={fp}
                data-testid={`nodes-item-${fp}`}
                style={{
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '8px 10px',
                  backgroundColor: 'var(--bg-app)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px'
                }}
              >
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                    {node.display_name}
                  </span>
                  {node.previous_name && (
                    <span style={{ ...chip, color: 'var(--accent-warning)' }}>
                      antes: {node.previous_name}
                    </span>
                  )}
                  <span
                    style={{
                      ...chip,
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      color: isSameOwner ? 'var(--accent-success)' : 'var(--accent-warning)'
                    }}
                  >
                    {isSameOwner ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}
                    <span>{relationLabel(node.relation)}</span>
                  </span>
                  {node.name_collision && (
                    <span style={{ ...chip, color: 'var(--accent-warning)' }}>
                      Colisión de nombre
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span data-testid={`nodes-fp-${fp}`} style={chip} title={fp}>
                    {fpShort}
                  </span>
                  <select
                    aria-label={`Política para ${node.display_name}`}
                    data-testid={`nodes-policy-select-${fp}`}
                    value={node.policy}
                    disabled={busyFp === fp}
                    onChange={(e) => handlePolicyChange(fp, e.target.value)}
                    style={{ ...fieldStyle, maxWidth: '100%' }}
                  >
                    {POLICY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
