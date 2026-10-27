import React, { useState, useEffect, useMemo } from 'react';
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
  ArrowRight,
  Copy,
  Check,
  ExternalLink,
  Link,
  X,
  Info
} from 'lucide-react';

const LANE_COLORS = [
  '#6366f1', // Indigo (main)
  '#10b981', // Emerald
  '#f59e0b', // Amber
  '#06b6d4', // Cyan
  '#ec4899', // Pink
  '#8b5cf6', // Purple
  '#14b8a6'  // Teal
];

export function BranchConsensusView({ project, api }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [selectedBranch, setSelectedBranch] = useState('');
  const [selectedCommit, setSelectedCommit] = useState(null);
  const [hoveredCommit, setHoveredCommit] = useState(null);
  const [expandedSessions, setExpandedSessions] = useState({});
  const [copiedHash, setCopiedHash] = useState(null);

  // Modales locales
  const [isNewBranchModalOpen, setIsNewBranchModalOpen] = useState(false);
  const [newBranchName, setNewBranchName] = useState('');
  const [isRemoteModalOpen, setIsRemoteModalOpen] = useState(false);
  const [remoteUrlInput, setRemoteUrlInput] = useState('');

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
        if (res.git?.origin_url) {
          setRemoteUrlInput(res.git.origin_url);
        }
      }
    } catch (e) {
      console.error("Error al cargar control de cambios:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [project, api]);

  const copyToClipboard = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopiedHash(id);
    setTimeout(() => setCopiedHash(null), 2000);
  };

  const toggleSession = (sessionId) => {
    setExpandedSessions(prev => ({
      ...prev,
      [sessionId]: !prev[sessionId]
    }));
  };

  // Acciones Git
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
        if (res.requires_remote) {
          setIsRemoteModalOpen(true);
        }
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
        if (res.requires_remote) {
          setIsRemoteModalOpen(true);
        }
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
        setFeedback({ type: 'success', text: `Rama '${selectedBranch}' publicada exitosamente en el repositorio remoto.` });
        loadData();
      } else {
        if (res.requires_remote) {
          setIsRemoteModalOpen(true);
        }
        setFeedback({ type: 'error', text: res.error || 'Error al publicar rama' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  const handleMerge = async () => {
    if (!api || !project || !selectedBranch) return;
    if (selectedBranch === 'main') {
      alert("La rama actual ya es 'main'. Selecciona una rama de feature para fusionar hacia main.");
      return;
    }
    const ok = window.confirm(`¿Confirmas la fusión de la rama '${selectedBranch}' hacia 'main'?`);
    if (!ok) return;

    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.git_merge_action(project.project_uuid, selectedBranch, 'main');
      if (res.success) {
        setFeedback({ type: 'success', text: `Rama '${selectedBranch}' fusionada en 'main' exitosamente.` });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Conflicto o error al fusionar' });
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
    setFeedback(null);
    try {
      const res = await api.git_create_branch_action(project.project_uuid, newBranchName.trim());
      if (res.success) {
        setIsNewBranchModalOpen(false);
        setNewBranchName('');
        setFeedback({ type: 'success', text: `Rama '${res.branch}' creada y activada.` });
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

  const handleSetRemoteUrl = async (e) => {
    e.preventDefault();
    if (!api || !project || !remoteUrlInput.trim()) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.set_project_remote_url(project.project_uuid, remoteUrlInput.trim());
      if (res.success) {
        setIsRemoteModalOpen(false);
        setFeedback({ type: 'success', text: `Repositorio remoto origin configurado: ${remoteUrlInput.trim()}` });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Error al configurar remoto' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  // Cálculo del Grafo SVG Nativo
  const { commitsWithLanes, connections, maxLane } = useMemo(() => {
    const rawCommits = data?.git?.commits || [];
    if (rawCommits.length === 0) {
      return { commitsWithLanes: [], connections: [], maxLane: 0 };
    }

    // Asignación de carriles (swimlanes)
    const commitLanes = {};
    const commitIndices = {};
    let currentLaneCount = 0;

    // Indexar
    rawCommits.forEach((c, idx) => {
      commitIndices[c.hash] = idx;
    });

    // Asignar carril 0 a main o commit inicial
    const assignedCommits = rawCommits.map((c, idx) => {
      let lane = 0;
      if (commitLanes[c.hash] !== undefined) {
        lane = commitLanes[c.hash];
      } else {
        // Nueva rama detectada
        lane = currentLaneCount;
        commitLanes[c.hash] = lane;
        currentLaneCount++;
      }

      // Propagar carril al primer padre si no tiene asignado
      if (c.parents && c.parents.length > 0) {
        const firstParent = c.parents[0];
        if (commitLanes[firstParent] === undefined) {
          commitLanes[firstParent] = lane;
        }
        // Padres adicionales (merges) obtienen nuevos carriles si no los tienen
        for (let pIdx = 1; pIdx < c.parents.length; pIdx++) {
          const parentHash = c.parents[pIdx];
          if (commitLanes[parentHash] === undefined) {
            commitLanes[parentHash] = currentLaneCount++;
          }
        }
      }

      const color = LANE_COLORS[lane % LANE_COLORS.length];
      return {
        ...c,
        index: idx,
        lane,
        color
      };
    });

    // Generar conexiones Bezier entre hijos y padres
    const conns = [];
    assignedCommits.forEach((child) => {
      if (!child.parents || child.parents.length === 0) return;
      child.parents.forEach((parentHash, pIndex) => {
        const parent = assignedCommits.find(c => c.hash === parentHash || parentHash.startsWith(c.hash) || c.hash.startsWith(parentHash));
        if (parent) {
          conns.push({
            from: child,
            to: parent,
            isMerge: pIndex > 0,
            color: pIndex === 0 ? child.color : parent.color
          });
        }
      });
    });

    return {
      commitsWithLanes: assignedCommits,
      connections: conns,
      maxLane: Math.min(Math.max(currentLaneCount, 1), 6)
    };
  }, [data]);

  const ROW_HEIGHT = 48;
  const LANE_WIDTH = 22;
  const SVG_PADDING_LEFT = 20;

  if (loading && !data) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: '10px', color: 'var(--text-secondary)' }}>
        <RefreshCw size={18} className="spin" />
        <span>Cargando control de cambios y árbol de commits...</span>
      </div>
    );
  }

  const git = data?.git || {};
  const consensusSessions = data?.consensus_sessions || [];
  const currentBranch = git.current_branch || 'main';
  const branchesList = git.branches || [currentBranch];
  const originUrl = git.origin_url;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', gap: '16px', overflowY: 'auto', paddingRight: '4px' }}>
      {/* 1. Barra de Herramientas Git & Estado Remoto */}
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '14px 18px',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '12px',
        boxShadow: 'var(--shadow-sm)'
      }}>
        {/* Selector de Rama & Estado */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <GitBranch size={16} color="var(--accent-primary)" />
            <span style={{ fontSize: '13px', fontWeight: 600 }}>Rama activa:</span>
            <select
              value={selectedBranch}
              onChange={(e) => setSelectedBranch(e.target.value)}
              style={{
                backgroundColor: 'var(--bg-app)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--accent-primary)',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
                fontWeight: 600,
                padding: '5px 10px',
                borderRadius: 'var(--radius-sm)',
                outline: 'none',
                cursor: 'pointer'
              }}
            >
              {branchesList.map(b => (
                <option key={b} value={b}>{b} {b === currentBranch ? '(HEAD)' : ''}</option>
              ))}
            </select>
          </div>

          <button
            onClick={() => setIsNewBranchModalOpen(true)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '5px 10px',
              fontSize: '12px',
              fontWeight: 500,
              borderRadius: 'var(--radius-sm)',
              border: '1px dashed var(--border-subtle)',
              backgroundColor: 'transparent',
              color: 'var(--text-secondary)',
              cursor: 'pointer'
            }}
          >
            <Plus size={13} /> Nueva Rama
          </button>

          {/* Indicador de Remoto */}
          {originUrl ? (
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              padding: '3px 8px',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-muted)'
            }}>
              <Link size={12} color="var(--accent-success)" />
              <span style={{ maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {originUrl}
              </span>
              <button
                onClick={() => setIsRemoteModalOpen(true)}
                title="Editar URL de GitHub origin"
                style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 0 }}
              >
                ✏️
              </button>
            </div>
          ) : (
            <button
              onClick={() => setIsRemoteModalOpen(true)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                padding: '4px 8px',
                fontSize: '11px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--accent-warning)',
                backgroundColor: 'rgba(245, 158, 11, 0.08)',
                color: 'var(--accent-warning)',
                cursor: 'pointer',
                fontWeight: 600
              }}
            >
              <AlertTriangle size={12} /> Vincular Remoto GitHub
            </button>
          )}
        </div>

        {/* Botones de Operación Git */}
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
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              cursor: actionLoading ? 'wait' : 'pointer'
            }}
          >
            <RefreshCw size={13} className={actionLoading ? 'spin' : ''} /> Fetch
          </button>

          <button
            onClick={handlePull}
            disabled={actionLoading}
            title="Traer cambios del remoto (git pull)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 500,
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              cursor: actionLoading ? 'wait' : 'pointer'
            }}
          >
            <Download size={13} /> Pull
          </button>

          <button
            onClick={handleMerge}
            disabled={actionLoading || selectedBranch === 'main'}
            title="Fusionar rama de feature en 'main'"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 500,
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: selectedBranch === 'main' ? 'var(--bg-subtle)' : 'rgba(99, 102, 241, 0.12)',
              color: selectedBranch === 'main' ? 'var(--text-muted)' : 'var(--accent-primary)',
              cursor: (actionLoading || selectedBranch === 'main') ? 'not-allowed' : 'pointer'
            }}
          >
            <GitMerge size={13} /> Fusionar en Main
          </button>

          <button
            onClick={handlePush}
            disabled={actionLoading}
            title="Publicar rama al repositorio remoto (git push -u origin)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '6px 14px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: actionLoading ? 'wait' : 'pointer',
              boxShadow: 'var(--shadow-sm)'
            }}
          >
            <Upload size={13} /> Publicar Rama
          </button>
        </div>
      </div>

      {/* Banner de Feedback si hay acción */}
      {feedback && (
        <div style={{
          padding: '10px 14px',
          borderRadius: 'var(--radius-md)',
          fontSize: '12px',
          display: 'flex',
          alignItems: 'flex-start',
          gap: '8px',
          backgroundColor: feedback.type === 'success' ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
          border: `1px solid ${feedback.type === 'success' ? 'var(--accent-success)' : '#ef4444'}`,
          color: feedback.type === 'success' ? 'var(--accent-success)' : '#ef4444'
        }}>
          {feedback.type === 'success' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          <div style={{ flex: 1 }}>{feedback.text}</div>
          <button
            onClick={() => setFeedback(null)}
            style={{ background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0 }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* 2. Grafo Visual Interactivo de Commits & Ramas */}
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
            <GitCommit size={18} color="var(--accent-primary)" />
            <h3 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
              Grafo Visual de Commits & Ramas
            </h3>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              ({commitsWithLanes.length} commits registrados)
            </span>
          </div>

          {/* Leyenda de Ramas */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '11px' }}>
            {branchesList.slice(0, 4).map((b, bIdx) => (
              <div key={b} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                <span style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: LANE_COLORS[bIdx % LANE_COLORS.length]
                }} />
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{b}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Visual Graph Viewport */}
        {commitsWithLanes.length === 0 ? (
          <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            No hay commits registrados en este repositorio todavía.
          </div>
        ) : (
          <div style={{
            position: 'relative',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-app)',
            overflowX: 'auto',
            maxHeight: '440px',
            overflowY: 'auto'
          }}>
            {/* SVG Connections Layer */}
            <svg
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: `${SVG_PADDING_LEFT + (maxLane + 1) * LANE_WIDTH + 20}px`,
                height: `${commitsWithLanes.length * ROW_HEIGHT}px`,
                pointerEvents: 'none',
                zIndex: 1
              }}
            >
              <defs>
                <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                  <feGaussianBlur stdDeviation="3" result="blur" />
                  <feComposite in="SourceGraphic" in2="blur" operator="over" />
                </filter>
              </defs>

              {/* Conexiones Curvas entre Commits */}
              {connections.map((conn, idx) => {
                const x1 = SVG_PADDING_LEFT + conn.from.lane * LANE_WIDTH + 8;
                const y1 = conn.from.index * ROW_HEIGHT + ROW_HEIGHT / 2;
                const x2 = SVG_PADDING_LEFT + conn.to.lane * LANE_WIDTH + 8;
                const y2 = conn.to.index * ROW_HEIGHT + ROW_HEIGHT / 2;
                const midY = (y1 + y2) / 2;

                const pathD = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;

                return (
                  <path
                    key={`conn-${idx}`}
                    d={pathD}
                    stroke={conn.color}
                    strokeWidth="2.5"
                    strokeDasharray={conn.isMerge ? "4 3" : "none"}
                    fill="none"
                    opacity={hoveredCommit && (hoveredCommit.hash === conn.from.hash || hoveredCommit.hash === conn.to.hash) ? 1 : 0.75}
                  />
                );
              })}
            </svg>

            {/* Commits Rows List */}
            <div style={{ position: 'relative', zIndex: 2 }}>
              {commitsWithLanes.map((c) => {
                const isHovered = hoveredCommit?.hash === c.hash;
                const isSelected = selectedCommit?.hash === c.hash;
                const nodeX = SVG_PADDING_LEFT + c.lane * LANE_WIDTH + 8;
                const nodeY = c.index * ROW_HEIGHT + ROW_HEIGHT / 2;

                return (
                  <div
                    key={c.hash}
                    onMouseEnter={() => setHoveredCommit(c)}
                    onMouseLeave={() => setHoveredCommit(null)}
                    onClick={() => setSelectedCommit(isSelected ? null : c)}
                    style={{
                      height: `${ROW_HEIGHT}px`,
                      display: 'flex',
                      alignItems: 'center',
                      paddingLeft: `${SVG_PADDING_LEFT + (maxLane + 1) * LANE_WIDTH + 14}px`,
                      paddingRight: '16px',
                      borderBottom: '1px solid var(--border-subtle)',
                      backgroundColor: isSelected
                        ? 'rgba(99, 102, 241, 0.12)'
                        : isHovered
                        ? 'var(--bg-subtle)'
                        : 'transparent',
                      cursor: 'pointer',
                      transition: 'background-color 0.15s'
                    }}
                  >
                    {/* SVG Node Disc */}
                    <div
                      style={{
                        position: 'absolute',
                        left: `${nodeX - 7}px`,
                        top: `${nodeY - 7}px`,
                        width: '14px',
                        height: '14px',
                        borderRadius: '50%',
                        backgroundColor: 'var(--bg-app)',
                        border: `3px solid ${c.color}`,
                        boxShadow: (isHovered || c.is_head) ? `0 0 10px ${c.color}` : 'none',
                        transform: isHovered ? 'scale(1.25)' : 'scale(1)',
                        transition: 'transform 0.15s, box-shadow 0.15s',
                        zIndex: 10
                      }}
                    />

                    {/* Commit Content Row */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: '14px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                        {/* Ref chips (HEAD, Branches) */}
                        {c.refs && (
                          <span style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '10px',
                            fontWeight: 700,
                            padding: '2px 7px',
                            borderRadius: '10px',
                            backgroundColor: c.is_head ? 'var(--accent-primary)' : 'var(--bg-subtle)',
                            color: c.is_head ? '#ffffff' : 'var(--accent-primary)',
                            border: '1px solid var(--border-subtle)',
                            whiteSpace: 'nowrap'
                          }}>
                            <GitBranch size={10} />
                            {c.refs.replace(/[()]/g, '')}
                          </span>
                        )}

                        <span style={{
                          fontSize: '13px',
                          fontWeight: isSelected ? 600 : 500,
                          color: 'var(--text-primary)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap'
                        }}>
                          {c.message}
                        </span>
                      </div>

                      {/* Right Meta: Hash, Author, Date */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexShrink: 0, fontSize: '11px', color: 'var(--text-muted)' }}>
                        <span style={{ color: 'var(--text-secondary)' }}>{c.author}</span>
                        <span style={{ fontFamily: 'var(--font-mono)' }}>
                          {c.date ? new Date(c.date).toLocaleDateString() : ''}
                        </span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            copyToClipboard(c.hash, c.hash);
                          }}
                          title="Copiar hash del commit"
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '3px',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '10px',
                            padding: '2px 6px',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-subtle)',
                            backgroundColor: 'var(--bg-subtle)',
                            color: copiedHash === c.hash ? 'var(--accent-success)' : 'var(--text-primary)',
                            cursor: 'pointer'
                          }}
                        >
                          {copiedHash === c.hash ? <Check size={11} /> : <Copy size={11} />}
                          {c.hash}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Panel de Inspección de Commit Seleccionado */}
        {selectedCommit && (
          <div style={{
            backgroundColor: 'var(--bg-subtle)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-md)',
            padding: '14px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Info size={15} color="var(--accent-primary)" />
                <span style={{ fontSize: '13px', fontWeight: 600 }}>Inspección del Commit:</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--accent-primary)' }}>
                  {selectedCommit.hash}
                </span>
              </div>
              <button
                onClick={() => setSelectedCommit(null)}
                style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
              >
                <X size={14} />
              </button>
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-primary)', fontWeight: 500 }}>
              {selectedCommit.message}
            </div>
            <div style={{ display: 'flex', gap: '16px', fontSize: '11px', color: 'var(--text-muted)' }}>
              <span>Autor: <strong>{selectedCommit.author}</strong></span>
              <span>Fecha: <strong>{selectedCommit.date}</strong></span>
              <span>Padres: <strong>{selectedCommit.parents ? selectedCommit.parents.join(', ') : 'Ninguno'}</strong></span>
            </div>
          </div>
        )}
      </div>

      {/* 3. Sección de Consenso Dialéctico (3-Turn A2A RFC) */}
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
          <div>
            <h3 style={{ fontSize: '15px', fontWeight: 600, margin: '0 0 2px 0' }}>
              Consenso Dialéctico de Agentes (3-Turn A2A RFC)
            </h3>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
              Registro inmutable de acuerdos y síntesis de los agentes autónomos para este proyecto.
            </p>
          </div>
          <span style={{
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            backgroundColor: 'var(--bg-subtle)',
            padding: '2px 8px',
            borderRadius: '10px',
            color: 'var(--text-muted)'
          }}>
            {consensusSessions.length} sesiones
          </span>
        </div>

        {consensusSessions.length === 0 ? (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
            No se han registrado sesiones de consenso dialéctico aún para este proyecto.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {consensusSessions.map((session) => {
              const isExpanded = expandedSessions[session.session_id];
              const verdict = session.verdict_signal;
              const isAccepted = verdict === 'ACCEPT';
              const isCompromise = verdict === 'COMPROMISE';

              return (
                <div
                  key={session.session_id}
                  style={{
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'var(--bg-surface)',
                    overflow: 'hidden'
                  }}
                >
                  {/* Encabezado Sesión */}
                  <div
                    onClick={() => toggleSession(session.session_id)}
                    style={{
                      padding: '12px 16px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      cursor: 'pointer',
                      backgroundColor: 'var(--bg-subtle)'
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 700 }}>
                        {session.session_id}
                      </span>
                      {session.card_id && (
                        <span style={{ fontSize: '11px', color: 'var(--accent-primary)', fontWeight: 600 }}>
                          [{session.card_id}]
                        </span>
                      )}
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{
                        fontSize: '11px',
                        fontWeight: 700,
                        padding: '2px 8px',
                        borderRadius: '4px',
                        backgroundColor: isAccepted ? 'rgba(16, 185, 129, 0.15)' : isCompromise ? 'rgba(245, 158, 11, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                        color: isAccepted ? 'var(--accent-success)' : isCompromise ? 'var(--accent-warning)' : '#ef4444'
                      }}>
                        {verdict || 'EN CURSO'}
                      </span>
                      {session.agreed_branch && (
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-muted)' }}>
                          Rama: {session.agreed_branch}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Cuerpo Sesión (3 Turnos) */}
                  {isExpanded && (
                    <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      {session.turns.map((turn, tIdx) => (
                        <div
                          key={tIdx}
                          style={{
                            padding: '10px 12px',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-subtle)',
                            backgroundColor: 'var(--bg-app)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '4px'
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px' }}>
                            <strong style={{ color: 'var(--accent-primary)' }}>
                              Turno {tIdx + 1}: {turn.stage}
                            </strong>
                            <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                              {turn.sender_node_uuid}
                            </span>
                          </div>
                          <pre style={{
                            margin: 0,
                            fontFamily: 'var(--font-mono)',
                            fontSize: '11px',
                            color: 'var(--text-secondary)',
                            whiteSpace: 'pre-wrap',
                            lineHeight: 1.4
                          }}>
                            {turn.payload_rfc_yaml}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Modal: Nueva Rama */}
      {isNewBranchModalOpen && (
        <div
          onClick={() => setIsNewBranchModalOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            backdropFilter: 'blur(3px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 300,
            padding: '16px'
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              backgroundColor: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-lg)',
              padding: '20px',
              width: '100%',
              maxWidth: '420px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              boxShadow: 'var(--shadow-lg)'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>Crear Nueva Rama Local</h3>
              <button
                onClick={() => setIsNewBranchModalOpen(false)}
                style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
              >
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleCreateBranch} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>
                  Nombre de la Rama (ej: feature/telemetria-p2p)
                </label>
                <input
                  autoFocus
                  required
                  type="text"
                  value={newBranchName}
                  onChange={(e) => setNewBranchName(e.target.value)}
                  placeholder="feature/nombre-de-tarea"
                  style={{
                    width: '100%',
                    backgroundColor: 'var(--bg-app)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-primary)',
                    fontFamily: 'var(--font-mono)',
                    padding: '8px 12px',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '13px',
                    outline: 'none'
                  }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button
                  type="button"
                  onClick={() => setIsNewBranchModalOpen(false)}
                  style={{
                    padding: '7px 12px',
                    fontSize: '12px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    backgroundColor: 'transparent',
                    color: 'var(--text-primary)',
                    cursor: 'pointer'
                  }}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  style={{
                    padding: '7px 14px',
                    fontSize: '12px',
                    fontWeight: 600,
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: 'var(--accent-primary)',
                    color: '#ffffff',
                    cursor: 'pointer'
                  }}
                >
                  Crear y Activar
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Vincular Repositorio Remoto Origin */}
      {isRemoteModalOpen && (
        <div
          onClick={() => setIsRemoteModalOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            backdropFilter: 'blur(3px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 300,
            padding: '16px'
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              backgroundColor: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-lg)',
              padding: '22px',
              width: '100%',
              maxWidth: '480px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              boxShadow: 'var(--shadow-lg)'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Link size={18} color="var(--accent-primary)" />
                <h3 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>Vincular Repositorio Remoto GitHub</h3>
              </div>
              <button
                onClick={() => setIsRemoteModalOpen(false)}
                style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
              >
                <X size={16} />
              </button>
            </div>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.45 }}>
              Ingresa la URL del repositorio en GitHub para este proyecto (ej: <code>https://github.com/usuario/mi-repo.git</code>). Esto configurará el control remoto <code>origin</code> para poder publicar y descargar ramas.
            </p>
            <form onSubmit={handleSetRemoteUrl} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>
                  URL Remota (HTTPS / SSH)
                </label>
                <input
                  autoFocus
                  required
                  type="text"
                  value={remoteUrlInput}
                  onChange={(e) => setRemoteUrlInput(e.target.value)}
                  placeholder="https://github.com/usuario/mi-repositorio.git"
                  style={{
                    width: '100%',
                    backgroundColor: 'var(--bg-app)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-primary)',
                    fontFamily: 'var(--font-mono)',
                    padding: '9px 12px',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '12px',
                    outline: 'none'
                  }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button
                  type="button"
                  onClick={() => setIsRemoteModalOpen(false)}
                  style={{
                    padding: '7px 12px',
                    fontSize: '12px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    backgroundColor: 'transparent',
                    color: 'var(--text-primary)',
                    cursor: 'pointer'
                  }}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  style={{
                    padding: '7px 16px',
                    fontSize: '12px',
                    fontWeight: 600,
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: 'var(--accent-primary)',
                    color: '#ffffff',
                    cursor: 'pointer'
                  }}
                >
                  Guardar y Vincular
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
