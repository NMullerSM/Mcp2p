import React, { useState, useEffect } from 'react';
import {
  GitBranch,
  GitMerge,
  GitCommit,
  RefreshCw,
  Download,
  Upload,
  Plus,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Layers,
  ChevronDown,
  ChevronRight,
  ShieldAlert,
  ArrowRight
} from 'lucide-react';

export function BranchConsensusView({ project, api }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [expandedSessions, setExpandedSessions] = useState({});
  const [selectedBranch, setSelectedBranch] = useState('');
  const [isNewBranchModalOpen, setIsNewBranchModalOpen] = useState(false);
  const [newBranchName, setNewBranchName] = useState('');

  const loadData = async () => {
    if (!api || !project) return;
    try {
      setLoading(true);
      const res = await api.get_branches_and_consensus(project.project_uuid);
      if (res.success) {
        setData(res);
        if (res.git?.current_branch) {
          setSelectedBranch(res.git.current_branch);
        }
      }
    } catch (e) {
      console.error("Error al cargar ramas y consenso:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [project, api]);

  const toggleSession = (sessionId) => {
    setExpandedSessions(prev => ({
      ...prev,
      [sessionId]: !prev[sessionId]
    }));
  };

  const handleFetch = async () => {
    if (!api || !project) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.git_fetch_action(project.project_uuid);
      if (res.success) {
        setFeedback({ type: 'success', text: 'Fetch completado: referencias remotas actualizadas.' });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Error al ejecutar fetch' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  const handlePull = async () => {
    if (!api || !project) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.git_pull_action(project.project_uuid, selectedBranch);
      if (res.success) {
        setFeedback({ type: 'success', text: 'Pull completado exitosamente.' });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Error al ejecutar pull' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  const handlePush = async () => {
    if (!api || !project) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.git_push_action(project.project_uuid, selectedBranch);
      if (res.success) {
        setFeedback({ type: 'success', text: `Rama ${selectedBranch} publicada en remoto.` });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Error al publicar rama (¿remoto configurado?)' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  const handleMergeToMain = async () => {
    if (!api || !project || !selectedBranch || selectedBranch === 'main') return;
    if (!confirm(`¿Deseas fusionar los cambios de la rama "${selectedBranch}" en "main"?`)) return;

    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.git_merge_action(project.project_uuid, selectedBranch, 'main');
      if (res.success) {
        setFeedback({ type: 'success', text: `¡Rama ${selectedBranch} fusionada exitosamente en main!` });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Error en el merge' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  const handleCreateBranch = async (e) => {
    e.preventDefault();
    if (!api || !project || !newBranchName.trim()) return;
    setActionLoading(true);
    try {
      const res = await api.git_create_branch_action(project.project_uuid, newBranchName.trim());
      if (res.success) {
        setIsNewBranchModalOpen(false);
        setNewBranchName('');
        setFeedback({ type: 'success', text: `Rama ${res.branch} creada exitosamente.` });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Error al crear rama' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  if (loading && !data) {
    return <div style={{ padding: '24px', color: 'var(--text-secondary)' }}>Cargando ramas y consenso dialéctico...</div>;
  }

  const git = data?.git || {};
  const commits = git.commits || [];
  const branches = git.branches || [];
  const sessions = data?.sessions || [];
  const disputes = data?.disputes || [];

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      gap: '16px',
      overflowY: 'auto',
      paddingRight: '4px'
    }}>
      {/* 1. BARRA DE OPERACIONES GIT & CONTROL DE RAMAS */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '12px 18px',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '12px',
        boxShadow: 'var(--shadow-sm)'
      }}>
        {/* Lado Izquierdo: Rama activa y selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <GitBranch size={16} color="var(--accent-primary)" />
            <span style={{ fontSize: '13px', fontWeight: 600 }}>Rama Activa:</span>
          </div>

          <select
            value={selectedBranch}
            onChange={(e) => setSelectedBranch(e.target.value)}
            style={{
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--accent-primary)',
              fontFamily: 'var(--font-mono)',
              fontSize: '12px',
              fontWeight: 600,
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-md)',
              padding: '5px 10px',
              outline: 'none',
              cursor: 'pointer'
            }}
          >
            {branches.map(b => (
              <option key={b} value={b}>
                {b} {b === git.current_branch ? '(HEAD)' : ''}
              </option>
            ))}
          </select>

          <button
            onClick={() => setIsNewBranchModalOpen(true)}
            title="Crear nueva rama Git"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '5px 10px',
              fontSize: '11px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'transparent',
              color: 'var(--text-secondary)',
              cursor: 'pointer'
            }}
          >
            <Plus size={12} /> Nueva Rama
          </button>
        </div>

        {/* Lado Derecho: Acciones Git (Fetch, Pull, Merge, Push) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button
            onClick={handleFetch}
            disabled={actionLoading}
            title="Sincronizar referencias remotas (git fetch)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 500,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              cursor: actionLoading ? 'not-allowed' : 'pointer'
            }}
          >
            <RefreshCw size={13} className={actionLoading ? 'spin' : ''} /> Fetch
          </button>

          <button
            onClick={handlePull}
            disabled={actionLoading}
            title="Traer últimos cambios del remoto (git pull)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 500,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              cursor: actionLoading ? 'not-allowed' : 'pointer'
            }}
          >
            <Download size={13} /> Pull
          </button>

          {selectedBranch !== 'main' && (
            <button
              onClick={handleMergeToMain}
              disabled={actionLoading}
              title={`Fusionar rama ${selectedBranch} en main`}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                padding: '6px 12px',
                fontSize: '12px',
                fontWeight: 600,
                borderRadius: 'var(--radius-md)',
                border: 'none',
                backgroundColor: 'rgba(16, 185, 129, 0.15)',
                color: 'var(--accent-success)',
                cursor: actionLoading ? 'not-allowed' : 'pointer'
              }}
            >
              <GitMerge size={13} /> Merge a main
            </button>
          )}

          <button
            onClick={handlePush}
            disabled={actionLoading}
            title="Publicar rama local en remoto (git push)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: actionLoading ? 'not-allowed' : 'pointer'
            }}
          >
            <Upload size={13} /> Publicar Rama
          </button>
        </div>
      </div>

      {/* Feedback Alert */}
      {feedback && (
        <div style={{
          padding: '10px 14px',
          borderRadius: 'var(--radius-md)',
          fontSize: '12px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          backgroundColor: feedback.type === 'success' ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
          color: feedback.type === 'success' ? 'var(--accent-success)' : 'var(--accent-danger)',
          border: `1px solid ${feedback.type === 'success' ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`
        }}>
          {feedback.type === 'success' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span>{feedback.text}</span>
        </div>
      )}

      {/* MODAL DE NUEVA RAMA */}
      {isNewBranchModalOpen && (
        <div style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 150
        }}>
          <div style={{
            backgroundColor: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-lg)',
            padding: '20px',
            width: '380px',
            maxWidth: '90vw'
          }}>
            <h4 style={{ margin: '0 0 12px 0', fontSize: '14px', fontWeight: 700 }}>Crear Nueva Rama Git</h4>
            <form onSubmit={handleCreateBranch} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>Nombre de la rama:</label>
                <input
                  type="text"
                  autoFocus
                  required
                  placeholder="feature/nombre-de-feature"
                  value={newBranchName}
                  onChange={(e) => setNewBranchName(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                    backgroundColor: 'var(--bg-subtle)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    color: 'var(--text-primary)',
                    outline: 'none'
                  }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                <button
                  type="button"
                  onClick={() => setIsNewBranchModalOpen(false)}
                  style={{
                    padding: '6px 12px',
                    fontSize: '12px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    backgroundColor: 'transparent',
                    color: 'var(--text-secondary)',
                    cursor: 'pointer'
                  }}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  style={{
                    padding: '6px 14px',
                    fontSize: '12px',
                    fontWeight: 600,
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: 'var(--accent-primary)',
                    color: '#ffffff',
                    cursor: 'pointer'
                  }}
                >
                  Crear Rama
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 2. ÁRBOL GRÁFICO VISUAL DE RAMAS (GIT TREE STEM DIAGRAM) */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '18px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Layers size={17} color="var(--accent-primary)" />
            <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 700 }}>Grafo Visual de Ramas y Commits</h4>
          </div>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {commits.length} commits en repositorio de código
          </span>
        </div>

        {commits.length === 0 ? (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
            No se han registrado commits aún en este proyecto.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '6px' }}>
            {commits.map((c, idx) => {
              const isMain = c.refs.includes('main');
              const isHead = c.is_head;
              const hasMerge = c.parents && c.parents.length > 1;

              return (
                <div
                  key={c.hash}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '12px',
                    padding: '10px 14px',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: isHead ? 'var(--bg-subtle)' : 'var(--bg-surface)',
                    border: `1px solid ${isHead ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                    transition: 'var(--transition)'
                  }}
                >
                  {/* Stem visual & Node circle */}
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '24px', flexShrink: 0 }}>
                    <div style={{
                      width: '14px',
                      height: '14px',
                      borderRadius: '50%',
                      backgroundColor: isHead ? 'var(--accent-primary)' : hasMerge ? 'var(--accent-success)' : 'var(--accent-warning)',
                      border: '2px solid var(--bg-surface)',
                      boxShadow: '0 0 0 2px var(--border-subtle)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: '#ffffff',
                      fontSize: '8px',
                      fontWeight: 800
                    }}>
                      {hasMerge ? 'M' : '•'}
                    </div>
                    {idx < commits.length - 1 && (
                      <div style={{ width: '2px', height: '28px', backgroundColor: 'var(--border-subtle)', marginTop: '4px' }} />
                    )}
                  </div>

                  {/* Commit info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: '4px' }}>
                      <span style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: '11px',
                        fontWeight: 700,
                        backgroundColor: 'rgba(99, 102, 241, 0.15)',
                        color: 'var(--accent-primary)',
                        padding: '1px 6px',
                        borderRadius: '3px'
                      }}>
                        {c.hash}
                      </span>

                      {c.refs && (
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '10px',
                          fontWeight: 600,
                          backgroundColor: 'rgba(16, 185, 129, 0.15)',
                          color: 'var(--accent-success)',
                          padding: '1px 6px',
                          borderRadius: '3px'
                        }}>
                          {c.refs}
                        </span>
                      )}

                      <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                        por <strong>{c.author}</strong>
                      </span>

                      <span style={{ fontSize: '10px', color: 'var(--text-muted)', marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>
                        {c.date}
                      </span>
                    </div>

                    <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                      {c.message}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 3. HISTORIAL DE CONSENSO DIALÉCTICO RFC (QUÉ SE ACEPTÓ, QUÉ NO Y QUÉ CAMBIÓ) */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '18px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <CheckCircle2 size={17} color="var(--accent-success)" />
            <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 700 }}>Consenso Dialéctico de Agentes (RFC 3 Turnos)</h4>
          </div>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            Registro inmutable de acuerdos y propuestas del enjambre
          </span>
        </div>

        {sessions.length === 0 ? (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
            No se han registrado sesiones de negociación dialéctica aún.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {sessions.map((sess) => {
              const isExpanded = Boolean(expandedSessions[sess.session_id]);
              const isAgreed = sess.status === 'AGREED';
              const isRejected = sess.status === 'REJECTED';

              return (
                <div
                  key={sess.session_id}
                  style={{
                    backgroundColor: 'var(--bg-surface)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    overflow: 'hidden',
                    transition: 'var(--transition)'
                  }}
                >
                  {/* Session Header Card */}
                  <div
                    onClick={() => toggleSession(sess.session_id)}
                    style={{
                      padding: '12px 16px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      cursor: 'pointer',
                      backgroundColor: isExpanded ? 'var(--bg-subtle)' : 'transparent',
                      gap: '10px'
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                      {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}

                      <span style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: '11px',
                        fontWeight: 700,
                        backgroundColor: 'var(--bg-card)',
                        border: '1px solid var(--border-subtle)',
                        padding: '2px 6px',
                        borderRadius: '3px'
                      }}>
                        {sess.card_id || sess.session_id}
                      </span>

                      <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {sess.component}: {sess.intent || 'Coordinación de arquitectura'}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                      {sess.agreed_branch && (
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '11px',
                          color: 'var(--accent-primary)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px'
                        }}>
                          <GitBranch size={11} /> {sess.agreed_branch}
                        </span>
                      )}

                      <span style={{
                        fontSize: '10px',
                        fontWeight: 700,
                        padding: '3px 8px',
                        borderRadius: 'var(--radius-sm)',
                        backgroundColor: isAgreed ? 'rgba(16, 185, 129, 0.15)' : isRejected ? 'rgba(239, 68, 68, 0.15)' : 'rgba(245, 158, 11, 0.15)',
                        color: isAgreed ? 'var(--accent-success)' : isRejected ? 'var(--accent-danger)' : 'var(--accent-warning)'
                      }}>
                        {isAgreed ? 'ACORDADO (AGREED)' : isRejected ? 'RECHAZADO' : 'EN DISCUSIÓN'}
                      </span>
                    </div>
                  </div>

                  {/* Expanded 3-Turn Sequence */}
                  {isExpanded && (
                    <div style={{
                      padding: '16px',
                      borderTop: '1px solid var(--border-subtle)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '12px',
                      backgroundColor: 'var(--bg-surface)'
                    }}>
                      {sess.turns.map((t, tIdx) => {
                        const isTurn1 = t.stage === 'PROPOSAL';
                        const isTurn2 = t.stage === 'COUNTER_PROPOSAL';
                        const isTurn3 = t.stage === 'RESOLUTION';

                        return (
                          <div
                            key={tIdx}
                            style={{
                              backgroundColor: 'var(--bg-card)',
                              border: '1px solid var(--border-subtle)',
                              borderRadius: 'var(--radius-sm)',
                              padding: '12px 14px',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: '6px'
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                <span style={{
                                  fontSize: '10px',
                                  fontFamily: 'var(--font-mono)',
                                  fontWeight: 700,
                                  padding: '1px 5px',
                                  borderRadius: '3px',
                                  backgroundColor: isTurn1 ? 'rgba(99, 102, 241, 0.2)' : isTurn2 ? 'rgba(245, 158, 11, 0.2)' : 'rgba(16, 185, 129, 0.2)',
                                  color: isTurn1 ? 'var(--accent-primary)' : isTurn2 ? 'var(--accent-warning)' : 'var(--accent-success)'
                                }}>
                                  TURNO {t.turn}: {t.stage}
                                </span>
                                <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                                  por <strong>{t.sender_node}</strong>
                                </span>
                              </div>

                              {t.verdict_signal && (
                                <span style={{
                                  fontSize: '10px',
                                  fontWeight: 700,
                                  fontFamily: 'var(--font-mono)',
                                  padding: '2px 6px',
                                  borderRadius: '3px',
                                  backgroundColor: t.verdict_signal === 'ACCEPT' ? 'rgba(16, 185, 129, 0.2)' : t.verdict_signal === 'COMPROMISE' ? 'rgba(245, 158, 11, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                                  color: t.verdict_signal === 'ACCEPT' ? 'var(--accent-success)' : t.verdict_signal === 'COMPROMISE' ? 'var(--accent-warning)' : 'var(--accent-danger)'
                                }}>
                                  Veredicto: {t.verdict_signal}
                                </span>
                              )}
                            </div>

                            {/* Contenido del Turno */}
                            {t.rationale && (
                              <div style={{ fontSize: '12px', color: 'var(--text-primary)' }}>
                                <strong style={{ color: 'var(--text-secondary)' }}>Motivación: </strong>{t.rationale}
                              </div>
                            )}

                            {t.critique && (
                              <div style={{ fontSize: '12px', color: 'var(--text-primary)' }}>
                                <strong style={{ color: 'var(--text-secondary)' }}>Evaluación Crítica: </strong>{t.critique}
                              </div>
                            )}

                            {t.synthesis_notes && (
                              <div style={{ fontSize: '12px', color: 'var(--text-primary)' }}>
                                <strong style={{ color: 'var(--accent-success)' }}>Síntesis Acordada: </strong>{t.synthesis_notes}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
