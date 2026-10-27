import React, { useState, useEffect } from 'react';
import { Users, Check, X, Shield, Clock, Share2, Copy } from 'lucide-react';

export function CollaboratorsView({ project, api }) {
  const [collaborators, setCollaborators] = useState([]);
  const [inviteUrl, setInviteUrl] = useState('');
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);

  const loadCollaborators = () => {
    if (!api || !project) return;
    setLoading(true);
    api.get_project_collaborators(project.project_uuid).then(res => {
      if (res.success) {
        setCollaborators(res.collaborators);
      }
      setLoading(false);
    });

    api.generate_p2p_invite(project.project_uuid).then(res => {
      if (res.success) {
        setInviteUrl(res.invite_url);
      }
    });
  };

  useEffect(() => {
    loadCollaborators();
  }, [project, api]);

  const handleApprove = async (nodeUuid) => {
    if (!api || !project) return;
    const res = await api.approve_collaborator(project.project_uuid, nodeUuid);
    if (res.success) {
      loadCollaborators();
    } else {
      alert("Error aprobando colaborador: " + res.error);
    }
  };

  const handleReject = async (nodeUuid) => {
    if (!api || !project) return;
    const res = await api.reject_collaborator(project.project_uuid, nodeUuid);
    if (res.success) {
      loadCollaborators();
    } else {
      alert("Error rechazando colaborador: " + res.error);
    }
  };

  const copyInvite = () => {
    navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '20px' }}>
      {/* Invite Box */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        padding: '16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '14px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Share2 size={20} color="var(--accent-primary)" />
          <div>
            <div style={{ fontSize: '13px', fontWeight: 600 }}>Invitar a un nuevo colaborador</div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
              Comparte el enlace P2P firmado; tú tendrás el control para aceptarlo cuando se conecte.
            </div>
          </div>
        </div>
        <button
          onClick={copyInvite}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '7px 14px',
            fontSize: '12px',
            fontWeight: 600,
            borderRadius: 'var(--radius-sm)',
            border: 'none',
            backgroundColor: copied ? 'var(--accent-success)' : 'var(--accent-primary)',
            color: '#ffffff',
            cursor: 'pointer'
          }}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? '¡Copiado!' : 'Copiar Enlace'}
        </button>
      </div>

      {/* Collaborators List */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <h4 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>
          Colaboradores del Proyecto ({collaborators.length})
        </h4>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {collaborators.map((c) => {
            const isPending = c.status === 'pending_approval';
            const isOwner = c.role === 'owner';

            return (
              <div
                key={c.node_uuid}
                style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px 18px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '12px'
                }}
              >
                {/* Node info */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <div style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: '50%',
                    backgroundColor: 'var(--bg-subtle)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: isOwner ? 'var(--accent-primary)' : 'var(--text-secondary)'
                  }}>
                    {isOwner ? <Shield size={18} /> : <Users size={18} />}
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
                        backgroundColor: isOwner ? 'rgba(79, 70, 229, 0.15)' : 'var(--bg-subtle)',
                        color: isOwner ? 'var(--accent-primary)' : 'var(--text-secondary)'
                      }}>
                        {c.role.toUpperCase()}
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

                      {/* Human-in-the-Loop Action */}
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
                    <span style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      color: 'var(--accent-success)',
                      backgroundColor: 'rgba(16, 185, 129, 0.12)',
                      padding: '4px 8px',
                      borderRadius: 'var(--radius-sm)',
                      fontWeight: 600
                    }}>
                      <Check size={12} /> Aprobado
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
