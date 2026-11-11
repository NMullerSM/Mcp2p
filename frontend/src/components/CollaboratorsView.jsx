import React, { useState, useEffect } from 'react';
import { Users, Check, X, Shield, Clock, Share2, Copy, Bot, Cpu, Radio, Trash2 } from 'lucide-react';

export function CollaboratorsView({ project, api }) {
  const [collaborators, setCollaborators] = useState([]);
  const [mcpInfo, setMcpInfo] = useState(null);
  const [connectedAgents, setConnectedAgents] = useState([]);
  const [inviteUrl, setInviteUrl] = useState('');
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);

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

  const copyInvite = () => {
    if (!inviteUrl) return;
    navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

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
              Colaboradores del Proyecto ({collaborators.length})
            </h4>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {collaborators.map((c) => {
            const isPending = c.status === 'pending_approval';
            const isOwner = c.role === 'owner';

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
            Agentes Conectados al Enjambre ({connectedAgents.length})
          </div>

          {connectedAgents.length === 0 ? (
            <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
              Iniciando pasarela MCP... Ningún agente conectado aún.
            </div>
          ) : (
            connectedAgents.map((ag) => (
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
