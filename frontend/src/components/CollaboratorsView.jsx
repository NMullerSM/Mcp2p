import React, { useState, useEffect } from 'react';
import { Users, Check, X, Shield, Clock, Share2, Copy, Bot, Cpu, Radio, Trash2, Lock, ClipboardPaste } from 'lucide-react';

/**
 * Formatea el tiempo restante de un bloqueo de solicitud de unión (`locked_until`,
 * ISO-8601 UTC calculado por el backend en get_project_collaborators -- ver
 * api_bridge.py, que reutiliza get_join_rate_limit_state en vez de duplicar los
 * umbrales de bloqueo aquí) como una cadena legible en español, p. ej. "29min" o
 * "23h 40min". Devuelve null si no hay bloqueo vigente (incluyendo cuando ya expiró
 * entre la carga de datos y el render).
 */
export function formatLockRemaining(lockedUntilIso) {
  if (!lockedUntilIso) return null;
  const until = new Date(lockedUntilIso).getTime();
  if (Number.isNaN(until)) return null;
  const remainingMs = until - Date.now();
  if (remainingMs <= 0) return null;
  const totalMinutes = Math.ceil(remainingMs / 60000);
  if (totalMinutes >= 60) {
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return minutes > 0 ? `${hours}h ${minutes}min` : `${hours}h`;
  }
  return `${totalMinutes}min`;
}

export function CollaboratorsView({ project, api, searchQuery = '' }) {
  const [collaborators, setCollaborators] = useState([]);
  const [mcpInfo, setMcpInfo] = useState(null);
  const [connectedAgents, setConnectedAgents] = useState([]);
  const [inviteUrl, setInviteUrl] = useState('');
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  // Ingesta manual (nivel 2) de un código de solicitud de unión pegado por el dueño.
  const [requestCodeInput, setRequestCodeInput] = useState('');
  const [ingesting, setIngesting] = useState(false);
  const [ingestMessage, setIngestMessage] = useState(null); // { type: 'success' | 'error', text }

  const loadData = async (isInitial = false) => {
    if (!api || !project) return;
    if (isInitial) setLoading(true);

    try {
      const [collabRes, inviteRes, mcpRes] = await Promise.allSettled([
        api.get_project_collaborators(project.project_uuid),
        api.generate_p2p_invite(project.project_uuid),
        api.get_mcp_and_agents_status(project.project_uuid)
      ]);

      if (collabRes.status === 'fulfilled' && collabRes.value?.success) {
        setCollaborators(collabRes.value.collaborators || []);
      }
      if (inviteRes.status === 'fulfilled' && inviteRes.value?.success) {
        setInviteUrl(inviteRes.value.invite_url || '');
      }
      if (mcpRes.status === 'fulfilled' && mcpRes.value?.success) {
        setMcpInfo(mcpRes.value.mcp_server || null);
        setConnectedAgents(mcpRes.value.connected_agents || []);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData(true);
  }, [project, api]);

  const handleApprove = async (nodeUuid) => {
    if (!api || !project) return;
    const res = await api.approve_collaborator(project.project_uuid, nodeUuid);
    if (res.success) {
      loadData(false);
    } else {
      alert("Error aprobando colaborador: " + res.error);
    }
  };

  const handleReject = async (nodeUuid) => {
    if (!api || !project) return;
    const res = await api.reject_collaborator(project.project_uuid, nodeUuid);
    if (res.success) {
      loadData(false);
    } else {
      alert("Error rechazando colaborador: " + res.error);
    }
  };

  const handleRemove = async (nodeUuid) => {
    if (!api || !project) return;
    if (!window.confirm("¿Estás seguro de que deseas desvincular a este colaborador del proyecto?")) return;
    try {
      const res = await api.remove_collaborator(project.project_uuid, nodeUuid);
      if (res.success) {
        loadData(false);
      } else {
        alert("Error removiendo colaborador: " + (res.error || "Error desconocido"));
      }
    } catch (err) {
      alert("Error removiendo colaborador: " + (err.message || String(err)));
    }
  };

  // Hallazgo C1 de la revisión final: sin esto, el dueño no tenía ninguna forma de
  // ingresar el código de solicitud que JoinRequestPending le pide al solicitante
  // compartir "fuera de banda", así que el flujo manual (el único que funciona sin
  // transporte en vivo) nunca llegaba a mostrar una fila pendiente que aprobar. Se pasa
  // el project_uuid de ESTE proyecto como destino explícito; el backend sigue exigiendo
  // que coincida con el project_uuid firmado dentro del código.
  const handleIngestRequestCode = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    const code = requestCodeInput.trim();
    if (!api || !project || !code || ingesting) return;
    setIngesting(true);
    setIngestMessage(null);
    try {
      const res = await api.ingest_join_request(code, project.project_uuid);
      if (res?.success) {
        setRequestCodeInput('');
        setIngestMessage({
          type: 'success',
          text: res.status === 'approved'
            ? 'Este solicitante ya estaba aprobado en el proyecto.'
            : 'Solicitud registrada. Revísala abajo para aceptarla o rechazarla.'
        });
        await loadData(false);
      } else if (res?.status === 'locked') {
        const remaining = formatLockRemaining(res.locked_until);
        setIngestMessage({
          type: 'error',
          text: 'Solicitud bloqueada temporalmente por rechazos previos' + (remaining ? ` (${remaining} restantes).` : '.')
        });
      } else {
        setIngestMessage({ type: 'error', text: res?.error || 'No se pudo registrar el código de solicitud.' });
      }
    } catch (err) {
      setIngestMessage({ type: 'error', text: 'Error de comunicación: ' + (err?.message || String(err)) });
    } finally {
      setIngesting(false);
    }
  };

  const copyInvite = () => {
    if (!inviteUrl) return;
    navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const normalizedQuery = (searchQuery || '').trim().toLowerCase();
  const filteredCollaborators = normalizedQuery
    ? collaborators.filter(c =>
        [c.developer_name, c.github_username, c.node_uuid, c.role, c.node_kind, c.status]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(normalizedQuery)
      )
    : collaborators;

  const filteredAgents = normalizedQuery
    ? connectedAgents.filter(ag =>
        [ag.developer_name, ag.agent_id, ag.node_uuid, ag.node_kind, ag.model]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(normalizedQuery)
      )
    : connectedAgents;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '18px', overflowY: 'auto', paddingRight: '4px', scrollbarGutter: 'stable' }}>
      {loading && !collaborators.length && !inviteUrl ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }} data-testid="collaborators-skeleton">
          <div style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', minHeight: '76px', opacity: 0.6 }} />
          <div style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', minHeight: '160px', opacity: 0.6 }} />
          <div style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', minHeight: '160px', opacity: 0.6 }} />
        </div>
      ) : (
        <>
      {/* 1. SECCIÓN INVITACIÓN P2P */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '16px 20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '14px',
        boxShadow: 'var(--shadow-sm)'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-primary)',
            flexShrink: 0
          }}>
            <Share2 size={18} />
          </div>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              Invitar a un nuevo colaborador al proyecto
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              Comparte el enlace P2P criptográfico; tú tienes el control exclusivo para aceptarlo cuando se conecte.
            </div>
          </div>
        </div>
        <button
          onClick={copyInvite}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            padding: '7px 16px',
            fontSize: '12px',
            fontWeight: 600,
            borderRadius: 'var(--radius-sm)',
            border: 'none',
            backgroundColor: copied ? 'var(--accent-success)' : 'var(--accent-primary)',
            color: '#ffffff',
            cursor: 'pointer',
            boxShadow: 'var(--shadow-sm)'
          }}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? '¡Enlace Copiado!' : 'Copiar Enlace P2P'}
        </button>
      </div>

      {/* 1b. SECCIÓN INGESTA MANUAL DE CÓDIGO DE SOLICITUD (nivel 2) */}
      <form
        onSubmit={handleIngestRequestCode}
        data-testid="ingest-request-code-form"
        style={{
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-lg)',
          padding: '16px 20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
          boxShadow: 'var(--shadow-sm)'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-primary)',
            flexShrink: 0
          }}>
            <ClipboardPaste size={18} />
          </div>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              Pegar código de solicitud
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              Si alguien te envió su código de solicitud de unión (por chat, email, etc.), pégalo aquí para revisarlo.
            </div>
          </div>
        </div>

        {ingestMessage && (
          <div
            data-testid="ingest-request-code-message"
            role={ingestMessage.type === 'error' ? 'alert' : 'status'}
            style={{
              padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '12px',
              backgroundColor: ingestMessage.type === 'success' ? 'rgba(var(--accent-primary-rgb), 0.15)' : 'rgba(239, 68, 68, 0.15)',
              color: ingestMessage.type === 'success' ? 'var(--accent-success)' : 'var(--accent-danger)',
              border: `1px solid ${ingestMessage.type === 'success' ? 'rgba(var(--accent-primary-rgb), 0.3)' : 'rgba(239, 68, 68, 0.3)'}`
            }}
          >
            {ingestMessage.text}
          </div>
        )}

        <div style={{ display: 'flex', gap: '8px' }}>
          <input
            type="text"
            aria-label="Código de solicitud de unión"
            placeholder="Pega aquí el código de solicitud"
            value={requestCodeInput}
            onChange={(e) => setRequestCodeInput(e.target.value)}
            style={{
              flex: 1,
              minWidth: 0,
              padding: '8px 10px',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              outline: 'none'
            }}
          />
          <button
            type="submit"
            disabled={ingesting || !requestCodeInput.trim()}
            style={{
              padding: '7px 16px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: ingesting || !requestCodeInput.trim() ? 'not-allowed' : 'pointer',
              opacity: ingesting || !requestCodeInput.trim() ? 0.6 : 1,
              whiteSpace: 'nowrap',
              boxShadow: 'var(--shadow-sm)'
            }}
          >
            {ingesting ? 'Registrando...' : 'Registrar Solicitud'}
          </button>
        </div>
      </form>

      {/* 2. SECCIÓN COLABORADORES HUMANOS */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '18px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
        boxShadow: 'var(--shadow-sm)'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Users size={17} color="var(--accent-primary)" />
            <h4 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
              Colaboradores del Proyecto ({filteredCollaborators.length})
            </h4>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {filteredCollaborators.length === 0 ? (
            <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
              {normalizedQuery ? 'Ningún colaborador coincide con la búsqueda.' : 'Aún no hay colaboradores registrados.'}
            </div>
          ) : filteredCollaborators.map((c) => {
            const isPending = c.status === 'pending_approval';
            const isOwner = c.role === 'owner';
            // locked_until viene precalculado por el backend (get_project_collaborators,
            // reutilizando get_join_rate_limit_state) para cualquier fila no aprobada --
            // normalmente aparece en una fila 'rejected' (el estado real tras 3+
            // rechazos: record_rejection marca la fila como rejected en la MISMA
            // operación que cruza el umbral de bloqueo, así que "pending_approval +
            // bloqueado" solo ocurre en una ventana de carrera, no en el flujo normal).
            // Es puramente informativo: el bloqueo rige si el SOLICITANTE puede volver a
            // pedir unirse, no si el DUEÑO puede aprobar/rechazar la fila que ya tiene
            // delante -- por eso nunca deshabilita ni oculta Aceptar/Rechazar/Remover.
            const lockRemaining = formatLockRemaining(c.locked_until);

            return (
              <div
                key={c.node_uuid}
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 16px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '12px'
                }}
              >
                {/* Node info */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <div style={{
                    width: '34px',
                    height: '34px',
                    borderRadius: '50%',
                    backgroundColor: 'var(--bg-subtle)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: isOwner ? 'var(--accent-primary)' : 'var(--text-secondary)'
                  }}>
                    {isOwner ? <Shield size={16} /> : <Users size={16} />}
                  </div>

                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '13px', fontWeight: 600 }}>
                        {c.developer_name || c.github_username || c.node_uuid}
                      </span>
                      {c.github_username && (
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                          (@{c.github_username})
                        </span>
                      )}
                      <span style={{
                        fontSize: '10px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 700,
                        padding: '1px 6px',
                        borderRadius: '3px',
                        backgroundColor: isOwner ? 'rgba(var(--accent-primary-rgb), 0.15)' : 'var(--bg-subtle)',
                        color: isOwner ? 'var(--accent-primary)' : 'var(--text-secondary)'
                      }}>
                        {c.role ? c.role.toUpperCase() : 'MEMBER'}
                      </span>
                      <span style={{
                        fontSize: '10px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 700,
                        padding: '1px 6px',
                        borderRadius: '3px',
                        backgroundColor: c.node_kind === 'human' ? 'var(--badge-lease)' : 'rgba(var(--accent-indigo-rgb), 0.12)',
                        color: c.node_kind === 'human' ? 'var(--badge-lease-text)' : 'var(--accent-indigo)'
                      }}>
                        {c.node_kind === 'human' ? 'HUMANO' : 'AGENTE'}
                      </span>
                    </div>

                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', marginTop: '2px' }}>
                      Nodo: {c.node_uuid}
                    </div>
                  </div>
                </div>

                {/* Status / HITL Actions */}
                <div>
                  {isPending ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '11px',
                        color: 'var(--accent-warning)',
                        backgroundColor: 'rgba(245, 158, 11, 0.12)',
                        padding: '4px 8px',
                        borderRadius: 'var(--radius-sm)'
                      }}>
                        <Clock size={12} /> Pendiente de Aprobación
                      </span>

                      {lockRemaining && (
                        <span
                          data-testid="collab-lock-state"
                          title="El solicitante no puede volver a pedir unirse mientras dure el bloqueo; esto no impide que apruebes o rechaces esta solicitud ya recibida"
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                            fontSize: '11px',
                            color: 'var(--accent-danger)',
                            backgroundColor: 'rgba(239, 68, 68, 0.12)',
                            padding: '4px 8px',
                            borderRadius: 'var(--radius-sm)',
                            fontWeight: 600
                          }}
                        >
                          <Lock size={12} /> Bloqueado ({lockRemaining} restantes)
                        </span>
                      )}

                      <button
                        onClick={() => handleApprove(c.node_uuid)}
                        title="Aprobar acceso a este colaborador"
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px',
                          padding: '5px 10px',
                          fontSize: '12px',
                          fontWeight: 600,
                          borderRadius: 'var(--radius-sm)',
                          border: 'none',
                          backgroundColor: 'var(--accent-success)',
                          color: '#ffffff',
                          cursor: 'pointer'
                        }}
                      >
                        <Check size={13} /> Aceptar
                      </button>

                      <button
                        onClick={() => handleReject(c.node_uuid)}
                        title="Rechazar solicitud"
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px',
                          padding: '5px 8px',
                          fontSize: '12px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-subtle)',
                          backgroundColor: 'transparent',
                          color: 'var(--accent-danger)',
                          cursor: 'pointer'
                        }}
                      >
                        <X size={13} />
                      </button>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '11px',
                        color: c.status === 'rejected' ? 'var(--accent-danger)' : 'var(--accent-success)',
                        backgroundColor: c.status === 'rejected' ? 'rgba(239, 68, 68, 0.12)' : 'rgba(var(--accent-primary-rgb), 0.12)',
                        padding: '4px 8px',
                        borderRadius: 'var(--radius-sm)',
                        fontWeight: 600
                      }}>
                        {c.status === 'rejected' ? <X size={12} /> : <Check size={12} />}
                        {c.status === 'rejected' ? 'Rechazado' : 'Aprobado'}
                      </span>
                      {c.status === 'rejected' && lockRemaining && (
                        <span
                          data-testid="collab-lock-state"
                          title="El solicitante no podrá volver a pedir unirse hasta que expire este bloqueo"
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                            fontSize: '11px',
                            color: 'var(--accent-danger)',
                            backgroundColor: 'rgba(239, 68, 68, 0.12)',
                            padding: '4px 8px',
                            borderRadius: 'var(--radius-sm)',
                            fontWeight: 600
                          }}
                        >
                          <Lock size={12} /> Bloqueado ({lockRemaining} restantes)
                        </span>
                      )}
                      {!isOwner && (
                        <button
                          onClick={() => handleRemove(c.node_uuid)}
                          title="Remover colaborador del proyecto"
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                            padding: '4px 8px',
                            fontSize: '11px',
                            fontWeight: 500,
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid rgba(239, 68, 68, 0.3)',
                            backgroundColor: 'transparent',
                            color: 'var(--accent-danger)',
                            cursor: 'pointer'
                          }}
                        >
                          <Trash2 size={12} /> Remover
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 3. SECCIÓN PASARELA MCP & AGENTES CONECTADOS */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '18px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
        boxShadow: 'var(--shadow-sm)'
      }}>
        {/* Encabezado MCP */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Cpu size={17} color="var(--accent-primary)" />
            <h4 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
              Pasarela MCP (Model Context Protocol) & Agentes Conectados
            </h4>
          </div>

          {/* Identificador MCP */}
          {mcpInfo && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '11px',
                fontFamily: 'var(--font-mono)',
                backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                color: 'var(--accent-success)',
                padding: '2px 8px',
                borderRadius: '10px',
                fontWeight: 600
              }}>
                <Radio size={11} /> {mcpInfo.server_name} ({mcpInfo.active_node_uuid})
              </span>
              <span style={{
                fontSize: '10px',
                fontFamily: 'var(--font-mono)',
                backgroundColor: 'var(--bg-subtle)',
                color: 'var(--text-muted)',
                padding: '2px 6px',
                borderRadius: '4px'
              }}>
                {mcpInfo.tools_count} Tools Expuestas
              </span>
            </div>
          )}
        </div>

        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.45 }}>
          El servidor MCP local permite a los agentes de IA interactuar con el tablero Kanban, reclamar tareas con leases atómicos y emitir turnos RFC sin consumir tokens.
        </p>

        {/* Lista de Agentes Conectados */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '4px' }}>
          <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
            Agentes Conectados al Enjambre ({filteredAgents.length})
          </div>

          {filteredAgents.length === 0 ? (
            <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
              {normalizedQuery ? 'Ningún agente coincide con la búsqueda.' : 'Iniciando pasarela MCP... Ningún agente conectado aún.'}
            </div>
          ) : (
            filteredAgents.map((ag) => (
              <div
                key={ag.node_uuid}
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 16px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '12px'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <div style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--accent-primary)',
                    flexShrink: 0
                  }}>
                    <Bot size={17} />
                  </div>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '13px', fontWeight: 600 }}>
                        {ag.developer_name || ag.agent_id}
                      </span>
                      <span style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: '10px',
                        backgroundColor: 'var(--bg-subtle)',
                        color: 'var(--accent-primary)',
                        padding: '1px 6px',
                        borderRadius: '3px'
                      }}>
                        {ag.agent_id}
                      </span>
                      <span style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: '10px',
                        fontWeight: 700,
                        backgroundColor: ag.node_kind === 'human' ? 'var(--badge-lease)' : 'rgba(var(--accent-indigo-rgb), 0.12)',
                        color: ag.node_kind === 'human' ? 'var(--badge-lease-text)' : 'var(--accent-indigo)',
                        padding: '1px 6px',
                        borderRadius: '3px'
                      }}>
                        {ag.node_kind === 'human' ? 'HUMANO' : 'AGENTE'}
                      </span>
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', marginTop: '2px' }}>
                      Nodo: {ag.node_uuid} • SLA de Revisión: {ag.reviewer_sla_hours}h
                    </div>
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                    color: 'var(--accent-success)'
                  }}>
                    <Radio size={10} /> En Línea
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
        </>
      )}
    </div>
  );
}
