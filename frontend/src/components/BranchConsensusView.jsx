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
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Link,
  X,
  Info,
  CheckSquare,
  Scale
} from 'lucide-react';
import { ViewSliderContent } from './ViewSlider';

const PALETTE = [
  '#63b0f4', // Azul claro
  '#73d13d', // Verde
  '#ff7a45', // Naranja
  '#b37feb', // Púrpura
  '#f759ab', // Rosa
  '#36cfc9', // Turquesa
  '#ffc53d', // Amarillo
  '#597ef7', // Azul real
  '#9254de', // Violeta
  '#43e8d8'  // Cian
];

export function BranchConsensusView({ project, api, initialBranch = '', onOpenCard, gitPulseTrigger = 0, searchQuery = '' }) {
  // Pestaña activa dentro de Control de Cambios: 'graph' (Grafo & Commits) vs 'consensus' (Consenso Dialéctico)
  const [subTab, setSubTab] = useState('graph');

  // Repositorio objetivo: 'app' (Código de la aplicación) vs 'gov' (Gobernanza OKF & Memoria Huérfana)
  const [repoTarget, setRepoTarget] = useState('app');

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [selectedBranch, setSelectedBranch] = useState(initialBranch || '');
  const [selectedCommit, setSelectedCommit] = useState(null);
  const [hoveredCommit, setHoveredCommit] = useState(null);
  const [expandedSessions, setExpandedSessions] = useState({});
  const [copiedHash, setCopiedHash] = useState(null);

  // Modales
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
        // Configurar rama inicial si no está seleccionada
        const currentRepoGit = repoTarget === 'gov' ? (res.git_gov || {}) : (res.git_app || res.git || {});
        if (!selectedBranch && currentRepoGit.current_branch) {
          setSelectedBranch(currentRepoGit.current_branch);
        }
        if (currentRepoGit.origin_url) {
          setRemoteUrlInput(currentRepoGit.origin_url);
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
  }, [project, api, gitPulseTrigger]);

  const handleAutoPublish = async (target = repoTarget) => {
    if (!api || !project) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.publish_project_to_github(project.project_uuid, target);
      if (res.success) {
        setFeedback({
          type: 'success',
          text: target === 'both'
            ? `Repositorios App (${res.app?.repo_url || ''}) y OKF (${res.gov?.repo_url || ''}) publicados exitosamente en GitHub.`
            : `Repositorio creado y publicado exitosamente en GitHub: ${res.repo_url} (Rama: ${res.branch})`
        });
        loadData();
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Error al auto-publicar repositorio en GitHub'
        });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  const handleSyncThreeTiers = async () => {
    if (!api || !project) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.sync_three_tiers(project.project_uuid);
      if (res.success) {
        const t1 = res.tier1_orphan || {};
        setFeedback({
          type: 'success',
          text: `Sincronización P2P completada: ${t1.flushed || 0} eventos volcados, ${t1.imported || 0} eventos remotos importados.`
        });
        loadData();
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Error durante la sincronización de 3 niveles'
        });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  const handleSetRemote = async (e) => {
    e.preventDefault();
    if (!api || !project || !remoteUrlInput.trim()) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.set_project_remote_url(project.project_uuid, remoteUrlInput.trim(), 'origin', repoTarget);
      if (res.success) {
        setIsRemoteModalOpen(false);
        setFeedback({ type: 'success', text: `URL remota origin configurada para repositorio ${repoTarget.toUpperCase()}.` });
        loadData();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Error al configurar URL remota' });
      }
    } catch (e) {
      setFeedback({ type: 'error', text: 'Error: ' + e });
    } finally {
      setActionLoading(false);
    }
  };

  // Si cambia el repoTarget, reajustar rama activa
  useEffect(() => {
    if (!data) return;
    const currentRepoGit = repoTarget === 'gov' ? (data.git_gov || {}) : (data.git_app || data.git || {});
    if (currentRepoGit.current_branch) {
      setSelectedBranch(currentRepoGit.current_branch);
    }
  }, [repoTarget, data]);

  // Si se provee una rama inicial externa
  useEffect(() => {
    if (initialBranch) {
      setSelectedBranch(initialBranch);
      setSubTab('graph');
    }
  }, [initialBranch]);

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

  // Acciones Git vinculadas al repositorio seleccionado (app o gov)
  const handleFetch = async () => {
    if (!api || !project) return;
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await api.git_fetch_action(project.project_uuid, repoTarget);
      if (res.success) {
        setFeedback({ type: 'success', text: `Fetch completado para repo ${repoTarget.toUpperCase()}: referencias actualizadas.` });
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
      const res = await api.git_pull_action(project.project_uuid, selectedBranch, repoTarget);
      if (res.success) {
        setFeedback({ type: 'success', text: `Pull completado exitosamente en rama '${selectedBranch}'.` });
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
      const res = await api.git_push_action(project.project_uuid, selectedBranch, repoTarget);
      if (res.success) {
        setFeedback({ type: 'success', text: `Cambios de '${selectedBranch}' enviados exitosamente al repositorio remoto.` });
        loadData();
      } else {
        if (res.requires_remote) {
          setIsRemoteModalOpen(true);
        }
        setFeedback({ type: 'error', text: res.error || 'Error al enviar cambios' });
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
      const res = await api.git_merge_action(project.project_uuid, selectedBranch, 'main', repoTarget);
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
      const res = await api.git_create_branch_action(project.project_uuid, newBranchName.trim(), repoTarget);
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

  // Repositorio activo seleccionado
  const activeGit = useMemo(() => {
    if (!data) return { branches: [], commits: [] };
    return repoTarget === 'gov' ? (data.git_gov || {}) : (data.git_app || data.git || {});
  }, [data, repoTarget]);

  const normalizedQuery = (searchQuery || '').trim().toLowerCase();

  // Lista de sesiones dialécticas filtrada por búsqueda modular
  const consensusSessions = useMemo(() => {
    const raw = data?.sessions || data?.consensus_sessions || [];
    if (!normalizedQuery) return raw;
    return raw.filter(s =>
      [s.session_id, s.topic, s.branch_name, s.card_id, s.status, s.final_decision, s.summary]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(normalizedQuery)
    );
  }, [data, normalizedQuery]);

  // Tarjetas del proyecto para enlaces bidireccionales
  const projectCards = useMemo(() => {
    return data?.cards || [];
  }, [data]);

  // Algoritmo de Trazado SVG inspirado en SourceGit (git-graph-plus), con reciclaje de
  // carriles: cuando una rama termina (se funde o no tiene más padres pendientes), su
  // carril queda libre para que una rama posterior en la lista lo reuse, en vez de crecer
  // para siempre hacia la derecha a medida que se abren y cierran ramas con el tiempo.
  const { commitsWithLanes, connections, maxLane } = useMemo(() => {
    const allCommits = activeGit.commits || [];
    const rawCommits = normalizedQuery
      ? allCommits.filter(c =>
          [c.hash, c.short_hash, c.message, c.author, ...(c.refs || [])]
            .filter(Boolean)
            .join(' ')
            .toLowerCase()
            .includes(normalizedQuery)
        )
      : allCommits;
    if (rawCommits.length === 0) {
      return { commitsWithLanes: [], connections: [], maxLane: 0 };
    }

    const commitLanes = {};     // hash -> carril ya asignado y dibujado
    const laneSlots = [];       // índice = carril; valor = hash que ese carril está esperando, o null si está libre
    let maxLaneUsed = 0;

    const claimFreeOrNewLane = () => {
      const free = laneSlots.indexOf(null);
      if (free !== -1) return free;
      laneSlots.push(null);
      return laneSlots.length - 1;
    };

    const assigned = rawCommits.map((c, idx) => {
      let lane = laneSlots.indexOf(c.hash);
      if (lane === -1) {
        lane = claimFreeOrNewLane();
      }
      laneSlots[lane] = null; // se consume la espera; se reserva de nuevo abajo si corresponde
      commitLanes[c.hash] = lane;
      maxLaneUsed = Math.max(maxLaneUsed, lane);

      // Propagar/reservar carriles para los padres
      if (c.parents && c.parents.length > 0) {
        const firstParent = c.parents[0];
        // Si ningún otro carril ya espera a este padre, seguimos ocupando el mismo carril.
        // Si ya lo espera otro carril (reconvergencia, p.ej. fusión a main), este carril
        // queda libre aquí mismo para que una rama más antigua lo reuse.
        if (laneSlots.indexOf(firstParent) === -1 && commitLanes[firstParent] === undefined) {
          laneSlots[lane] = firstParent;
        }
        for (let pIdx = 1; pIdx < c.parents.length; pIdx++) {
          const parentHash = c.parents[pIdx];
          if (laneSlots.indexOf(parentHash) === -1 && commitLanes[parentHash] === undefined) {
            const newLane = claimFreeOrNewLane();
            laneSlots[newLane] = parentHash;
          }
        }
      }
      // Commit raíz (sin padres): el carril ya quedó libre arriba, nada más que hacer.

      const color = PALETTE[lane % PALETTE.length];

      // Detección de tarjeta vinculada en mensaje de commit o rama
      let linkedCardId = null;
      const match = c.message.match(/TASK-\d+|CARD-\d+/i);
      if (match) {
        linkedCardId = match[0].toUpperCase();
      } else {
        // Chequear si alguna tarjeta tiene branch_ref igual a las refs del commit
        const matchingCard = projectCards.find(card => card.branch_ref && c.refs && c.refs.includes(card.branch_ref));
        if (matchingCard) {
          linkedCardId = matchingCard.card_id;
        }
      }

      return {
        ...c,
        index: idx,
        lane,
        color,
        linkedCardId
      };
    });

    // Conexiones curvas Bezier
    const conns = [];
    assigned.forEach((child) => {
      if (!child.parents || child.parents.length === 0) return;
      child.parents.forEach((parentHash, pIndex) => {
        const parent = assigned.find(c => c.hash === parentHash || parentHash.startsWith(c.hash) || c.hash.startsWith(parentHash));
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
      commitsWithLanes: assigned,
      // maxLane es el carril real más alto que de verdad se usó (con reciclaje ya aplicado
      // arriba), no un tope artificial desconectado de las posiciones reales de los puntos
      // — ese desacople era la causa de que los puntos de más carriles se dibujaran más a
      // la derecha de donde el texto asumía que terminaba el grafo, tapándolo.
      connections: conns,
      maxLane: Math.max(maxLaneUsed, 0)
    };
  }, [activeGit, projectCards, normalizedQuery]);

  const ROW_HEIGHT = 44;
  const LANE_WIDTH = 16;
  const SVG_PADDING_LEFT = 16;

  if (loading && !data) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: '10px', color: 'var(--text-secondary)' }}>
        <RefreshCw size={18} className="spin" />
        <span>Cargando control de cambios y árbol de commits...</span>
      </div>
    );
  }

  const branchesList = activeGit.branches || [activeGit.current_branch || 'main'];
  const originUrl = activeGit.origin_url;
  const isCurrentBranchPublished = Boolean(activeGit.is_tracking && activeGit.unpushed_count === 0);
  const gitAppOrigin = data?.git_app?.origin_url || (repoTarget === 'app' ? originUrl : null);
  const gitGovOrigin = data?.git_gov?.origin_url || (repoTarget === 'gov' ? originUrl : null);
  const bothOriginsConfigured = Boolean(gitAppOrigin && gitGovOrigin);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', gap: '14px', overflowY: 'auto', paddingRight: '4px' }}>
      
      {/* 1. SELECTOR SUPERIOR: GRAFO & COMMITS vs CONSENSO DIALÉCTICO */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '8px 12px',
        boxShadow: 'var(--shadow-sm)',
        flexWrap: 'wrap',
        gap: '10px'
      }}>
        {/* Switcher de Vistas */}
        <div style={{
          display: 'inline-flex',
          backgroundColor: 'var(--bg-subtle)',
          padding: '3px',
          borderRadius: 'var(--radius-md)',
          gap: '4px'
        }}>
          <button
            onClick={() => setSubTab('graph')}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 14px',
              fontSize: '12px',
              fontWeight: subTab === 'graph' ? 700 : 500,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: subTab === 'graph' ? 'var(--bg-surface)' : 'transparent',
              color: subTab === 'graph' ? 'var(--text-primary)' : 'var(--text-secondary)',
              cursor: 'pointer',
              boxShadow: subTab === 'graph' ? 'var(--shadow-sm)' : 'none',
              transition: 'var(--transition)'
            }}
          >
            <GitCommit size={14} color={subTab === 'graph' ? 'var(--accent-primary)' : 'currentColor'} />
            <span>Grafo & Commits</span>
          </button>

          <button
            onClick={() => setSubTab('consensus')}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 14px',
              fontSize: '12px',
              fontWeight: subTab === 'consensus' ? 700 : 500,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: subTab === 'consensus' ? 'var(--bg-surface)' : 'transparent',
              color: subTab === 'consensus' ? 'var(--text-primary)' : 'var(--text-secondary)',
              cursor: 'pointer',
              boxShadow: subTab === 'consensus' ? 'var(--shadow-sm)' : 'none',
              transition: 'var(--transition)'
            }}
          >
            <Scale size={14} color={subTab === 'consensus' ? 'var(--accent-success)' : 'currentColor'} />
            <span>Consenso Dialéctico ({consensusSessions.length})</span>
          </button>
        </div>

        {/* Si estamos en el Grafo, Selector de Doble Repositorio (App vs Gobernanza OKF) */}
        {subTab === 'graph' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>REPOSITORIO:</span>
            <select
              value={repoTarget}
              onChange={(e) => setRepoTarget(e.target.value)}
              title={repoTarget === 'gov' ? 'Repositorio de documentación' : 'Repositorio de desarrollo'}
              style={{
                // Mismo fix de contraste en modo oscuro que el select de Rama activa: el
                // cuadro cerrado usa el widget nativo salvo que se quite la apariencia.
                WebkitAppearance: 'none',
                MozAppearance: 'none',
                appearance: 'none',
                backgroundColor: 'var(--bg-app)',
                border: '1px solid var(--border-subtle)',
                color: repoTarget === 'gov' ? 'var(--brand-emerald)' : 'var(--accent-indigo)',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
                fontWeight: 600,
                padding: '5px 10px',
                borderRadius: 'var(--radius-sm)',
                outline: 'none',
                cursor: 'pointer'
              }}
            >
              <option value="app">Repositorio de desarrollo</option>
              <option value="gov">Repositorio de documentación</option>
            </select>
          </div>
        )}
      </div>

      {/* Migaja de pan para orientar de un vistazo en qué repo+rama se está trabajando */}
      {subTab === 'graph' && (
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          Proyecto {project?.name || ''} › {repoTarget === 'gov' ? 'Documentación' : 'Desarrollo'} › {selectedBranch}
        </div>
      )}

      {/* Banner de Feedback si hay acción */}
      {feedback && (
        <div style={{
          padding: '10px 14px',
          borderRadius: 'var(--radius-md)',
          fontSize: '12px',
          display: 'flex',
          alignItems: 'flex-start',
          gap: '8px',
          backgroundColor: feedback.type === 'success' ? 'rgba(var(--accent-primary-rgb), 0.12)' : 'rgba(239, 68, 68, 0.12)',
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

      {/* ======================================================== */}
      {/* SUBTAB 1: GRAFO VISUAL & COMMITS                         */}
      {/* ======================================================== */}
      {subTab === 'graph' && (
        <>
          {/* Barra de Herramientas Git Contextual */}
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
            {/* Rama Activa y Creador */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <GitBranch size={16} color={repoTarget === 'gov' ? 'var(--brand-emerald)' : 'var(--accent-indigo)'} />
                <span style={{ fontSize: '13px', fontWeight: 600 }}>Rama activa:</span>
                <select
                  value={selectedBranch}
                  onChange={(e) => setSelectedBranch(e.target.value)}
                  title={selectedBranch}
                  style={{
                    // Mismo fix de contraste en modo oscuro que el select de Estado: el
                    // cuadro cerrado usa el widget nativo salvo que se quite la apariencia.
                    WebkitAppearance: 'none',
                    MozAppearance: 'none',
                    appearance: 'none',
                    backgroundColor: 'var(--bg-app)',
                    border: '1px solid var(--border-subtle)',
                    color: repoTarget === 'gov' ? 'var(--brand-emerald)' : 'var(--accent-indigo)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '12px',
                    fontWeight: 600,
                    padding: '5px 10px',
                    borderRadius: 'var(--radius-sm)',
                    outline: 'none',
                    cursor: 'pointer',
                    // Tope de ancho fijo para que una rama con nombre largo (p.ej. las
                    // "orphan-conversa-memory-..." del repo de gobernanza) no estire esta
                    // fila y desalinee la barra de botones respecto al modo de desarrollo.
                    maxWidth: '200px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap'
                  }}
                >
                  {branchesList.map(b => (
                    <option key={b} value={b}>
                      {b} {b === activeGit.current_branch ? '(HEAD)' : ''} {b.startsWith('orphan-') ? '[HUÉRFANA]' : ''}
                    </option>
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
            </div>

            {/* Operaciones Git principales (Pull, Merge, Push); Fetch y el resto viven en el slider lateral */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>

              <button
                onClick={handlePull}
                disabled={actionLoading}
                title="Traer cambios del remoto a la rama local (git pull)"
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
                <Download size={13} /> Actualizar rama
              </button>

              {/* "Fusionar a main" no aplica al repositorio de gobernanza: el OKF es
                  meramente informativo y por diseño nunca gesta ramas propias, todo vive
                  directamente en main — no tiene sentido ofrecer un merge que nunca tendrá
                  nada que fusionar. Se oculta también cuando, por la razón que sea, no hay
                  otra rama real para elegir (en vez de mostrarlo deshabilitado sin motivo
                  visible). */}
              {repoTarget !== 'gov' && branchesList.length > 1 && (
                <button
                  onClick={handleMerge}
                  disabled={actionLoading || selectedBranch === 'main'}
                  title="Fusionar cambios de la rama seleccionada a main"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '6px 12px',
                    fontSize: '12px',
                    fontWeight: 500,
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    backgroundColor: selectedBranch === 'main' ? 'var(--bg-subtle)' : 'rgba(var(--accent-primary-rgb), 0.12)',
                    color: selectedBranch === 'main' ? 'var(--text-muted)' : 'var(--accent-primary)',
                    cursor: (actionLoading || selectedBranch === 'main') ? 'not-allowed' : 'pointer'
                  }}
                >
                  <GitMerge size={13} /> Fusionar a main
                </button>
              )}

              {/* Enviar los commits locales ya existentes al remoto ya configurado — una
                  acción recurrente ("push"), distinta de "Publicar repositorio" (sección de
                  abajo), que es la creación/vinculación del repositorio remoto por única vez.
                  Antes decía "Publicar Rama", lo que sonaba a esa acción única y confundía
                  sobre cuál botón usar para simplemente mandar cambios nuevos. */}
              {!isCurrentBranchPublished && (
                <button
                  onClick={handlePush}
                  disabled={actionLoading}
                  title="Envía los commits locales que todavía no están en el remoto (git push)"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '6px 14px',
                    fontSize: '12px',
                    fontWeight: 600,
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: repoTarget === 'gov' ? 'var(--brand-emerald)' : 'var(--accent-indigo)',
                    color: '#ffffff',
                    cursor: actionLoading ? 'wait' : 'pointer',
                    boxShadow: 'var(--shadow-sm)'
                  }}
                >
                  <Upload size={13} /> Enviar Cambios
                </button>
              )}
            </div>

          </div>

          {/* Aviso de commits sin enviar: cuando se comitea una card/ruling, MCP2P intenta
              pushear automáticamente, pero si ese envío automático falla (red, credenciales,
              remoto no configurado) el commit local queda guardado sin avisar en ningún lado
              — antes solo se sabía mirando el log de depuración del backend. Esto lo hace
              visible apenas se abre este panel, en vez de quedar en silencio. */}
          {repoTarget === 'gov' && activeGit.unpushed_count > 0 && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              backgroundColor: 'rgba(234, 179, 8, 0.1)',
              border: '1px solid rgba(234, 179, 8, 0.3)',
              borderRadius: 'var(--radius-md)',
              padding: '10px 14px',
              fontSize: '12px',
              color: '#eab308'
            }}>
              <AlertTriangle size={14} />
              <span>
                Hay {activeGit.unpushed_count} commit{activeGit.unpushed_count === 1 ? '' : 's'} de
                gobernanza sin enviar al remoto — puede ser que el envío automático al guardar
                haya fallado (red, credenciales, o el remoto todavía no está configurado). Usa
                "Enviar Cambios" arriba para reintentarlo.
              </span>
            </div>
          )}

          <ViewSliderContent
            title="Más acciones & Remoto Git"
            subtitle="Contenido secundario de Control de Cambios"
            data-testid="branch-slider"
          >
          <div>
            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '8px' }}>
              Actualizar
            </div>
            <button
              onClick={handleFetch}
              disabled={actionLoading}
              title="Traer referencias del remoto sin fusionar (git fetch)"
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
              <RefreshCw size={13} className={actionLoading ? 'spin' : ''} /> Traer cambios
            </button>
          </div>
          {/* Acción secundaria poco comprendida por el usuario: se saca de la fila
              primaria de botones y se guarda en el drawer, igual que las "3 Vías
              Dialécticas" en HumanApprovalsView. */}
          <div>
            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '8px' }}>
              Más acciones
            </div>
            <button
              onClick={handleSyncThreeTiers}
              disabled={actionLoading}
              title="Sincroniza el enjambre P2P en memoria, metadatos y git"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 14px',
                fontSize: '12px',
                fontWeight: 600,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid rgba(var(--accent-primary-rgb), 0.4)',
                backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                color: 'var(--accent-primary)',
                cursor: actionLoading ? 'wait' : 'pointer'
              }}
            >
              <RefreshCw size={13} className={actionLoading ? 'spin' : ''} /> Sincronizar P2P (3 Niveles)
            </button>
          </div>

          {/* Barra de Estado Remoto & Auto-Publicación */}
          <div style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-md)',
            padding: '10px 16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
            flexWrap: 'wrap',
            fontSize: '12px'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flex: 1 }}>
              <Link size={14} color="var(--text-secondary)" />
              <span style={{ fontWeight: 600, color: 'var(--text-muted)' }}>Remoto ({repoTarget.toUpperCase()}):</span>
              {originUrl ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                  <code style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '11px',
                    backgroundColor: 'var(--bg-subtle)',
                    padding: '3px 8px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-primary)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    maxWidth: '420px'
                  }}>
                    {originUrl}
                  </code>
                  <button
                    onClick={() => setIsRemoteModalOpen(true)}
                    style={{
                      border: 'none',
                      background: 'transparent',
                      color: 'var(--accent-primary)',
                      fontSize: '11px',
                      cursor: 'pointer',
                      padding: 0,
                      textDecoration: 'underline'
                    }}
                  >
                    Cambiar
                  </button>
                </div>
              ) : (
                <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                  Sin repositorio remoto origin vinculado en GitHub.
                </span>
              )}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              {!bothOriginsConfigured && (
                <button
                  onClick={() => handleAutoPublish('both')}
                  disabled={actionLoading}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 14px',
                    fontSize: '12px',
                    fontWeight: 600,
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: 'var(--accent-indigo)',
                    color: '#ffffff',
                    cursor: actionLoading ? 'wait' : 'pointer',
                    boxShadow: 'var(--shadow-sm)'
                  }}
                  title="Crea y publica automáticamente ambos repositorios (Desarrollo y Documentación OKF) y empuja la memoria conversacional"
                >
                  <Upload size={13} className={actionLoading ? 'spin' : ''} />
                  <span>⚡ Publicar Ambos (Desarrollo + Documentación)</span>
                </button>
              )}

              {!originUrl && (
                <>
                  <button
                    onClick={() => handleAutoPublish(repoTarget)}
                    disabled={actionLoading}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      padding: '6px 14px',
                      fontSize: '12px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: repoTarget === 'gov' ? 'var(--brand-emerald)' : 'var(--accent-indigo)',
                      color: '#ffffff',
                      cursor: actionLoading ? 'wait' : 'pointer',
                      boxShadow: 'var(--shadow-sm)'
                    }}
                    title={`Crea y publica solo el repositorio ${repoTarget.toUpperCase()} en GitHub`}
                  >
                    <Upload size={13} className={actionLoading ? 'spin' : ''} />
                    <span>Publicar Solo {repoTarget.toUpperCase()}</span>
                  </button>
                  <button
                    onClick={() => setIsRemoteModalOpen(true)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      padding: '5px 10px',
                      fontSize: '11px',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-subtle)',
                      color: 'var(--text-secondary)',
                      cursor: 'pointer'
                    }}
                  >
                    Configurar en Origin
                  </button>
                </>
              )}
            </div>
          </div>
          </ViewSliderContent>

          {/* Grafo de Commits SVG Nativo */}
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
                <GitCommit size={18} color={repoTarget === 'gov' ? 'var(--brand-emerald)' : 'var(--accent-indigo)'} />
                <h3 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
                  {repoTarget === 'app' ? 'Grafo de Commits del Repositorio de Desarrollo' : 'Grafo del Repositorio de Documentación'}
                </h3>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  ({commitsWithLanes.length} commits)
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
                      backgroundColor: PALETTE[bIdx % PALETTE.length]
                    }} />
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{b}</span>
                  </div>
                ))}
              </div>
            </div>

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
                maxHeight: '460px',
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
                  {/* Conexiones Curvas entre Commits (Algoritmo SourceGit) */}
                  {connections.map((conn, idx) => {
                    const x1 = SVG_PADDING_LEFT + conn.from.lane * LANE_WIDTH + 8;
                    const y1 = conn.from.index * ROW_HEIGHT + ROW_HEIGHT / 2;
                    const x2 = SVG_PADDING_LEFT + conn.to.lane * LANE_WIDTH + 8;
                    const y2 = conn.to.index * ROW_HEIGHT + ROW_HEIGHT / 2;
                    const midY = (y1 + y2) / 2;

                    // Curva Bezier cúbica suave
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
                            ? 'rgba(var(--accent-primary-rgb), 0.12)'
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
                            left: `${nodeX - 5}px`,
                            top: `${nodeY - 5}px`,
                            width: '10px',
                            height: '10px',
                            borderRadius: '50%',
                            backgroundColor: 'var(--bg-app)',
                            border: `2.5px solid ${c.color}`,
                            boxShadow: (isHovered || c.is_head) ? `0 0 10px ${c.color}` : 'none',
                            transform: isHovered ? 'scale(1.3)' : 'scale(1)',
                            transition: 'transform 0.15s, box-shadow 0.15s',
                            zIndex: 10
                          }}
                        />

                        {/* Commit Content Row */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: '14px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flexWrap: 'wrap' }}>
                            {/* Branch / Head chips */}
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
                                backgroundColor: c.is_head ? (repoTarget === 'gov' ? 'var(--brand-emerald)' : 'var(--accent-indigo)') : 'var(--bg-subtle)',
                                color: c.is_head ? '#ffffff' : 'var(--text-primary)',
                                border: '1px solid var(--border-subtle)',
                                whiteSpace: 'nowrap'
                              }}>
                                <GitBranch size={10} />
                                {c.refs.replace(/[()]/g, '')}
                              </span>
                            )}

                            {/* Enlace Bidireccional a Tarjeta Kanban */}
                            {c.linkedCardId && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onOpenCard && onOpenCard(c.linkedCardId);
                                }}
                                title={`Abrir detalles y criterios de la tarea ${c.linkedCardId}`}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: '4px',
                                  fontFamily: 'var(--font-mono)',
                                  fontSize: '10px',
                                  fontWeight: 700,
                                  padding: '2px 7px',
                                  borderRadius: '6px',
                                  border: '1px solid rgba(var(--accent-primary-rgb), 0.4)',
                                  backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                                  color: 'var(--accent-primary)',
                                  cursor: 'pointer'
                                }}
                              >
                                <CheckSquare size={11} /> {c.linkedCardId}
                              </button>
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

                          {/* Right Meta: Autor, Fecha, Hash */}
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
        </>
      )}

      {/* ======================================================== */}
      {/* SUBTAB 2: CONSENSO DIALÉCTICO (3-TURN A2A RFC)           */}
      {/* ======================================================== */}
      {subTab === 'consensus' && (
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
          <div>
            <h3 style={{ fontSize: '16px', fontWeight: 600, margin: '0 0 4px 0' }}>
              Consenso Dialéctico de Agentes (A2A 3-Turn RFC)
            </h3>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.45 }}>
              Registro inmutable de deliberaciones y síntesis de los agentes autónomos vinculadas a tarjetas Kanban y a la gobernanza OKF.
            </p>
          </div>

          {consensusSessions.length === 0 ? (
            <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              No hay sesiones de consenso registradas para este proyecto aún.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {consensusSessions.map((session) => {
                const isExpanded = expandedSessions[session.session_id];
                const verdict = session.verdict_signal || (session.status === 'AGREED' ? 'ACCEPT' : session.status);
                const isAccepted = verdict === 'ACCEPT' || verdict === 'AGREED';
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
                        padding: '14px 18px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        cursor: 'pointer',
                        backgroundColor: 'var(--bg-subtle)',
                        flexWrap: 'wrap',
                        gap: '10px'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: 700 }}>
                          {session.session_id}
                        </span>

                        {/* Enlace Bidireccional a Tarjeta Kanban */}
                        {session.card_id && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenCard && onOpenCard(session.card_id);
                            }}
                            title="Abrir detalles de la tarjeta vinculada"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              fontSize: '11px',
                              fontWeight: 700,
                              fontFamily: 'var(--font-mono)',
                              padding: '2px 8px',
                              borderRadius: '6px',
                              border: '1px solid rgba(var(--accent-primary-rgb), 0.4)',
                              backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                              color: 'var(--accent-primary)',
                              cursor: 'pointer'
                            }}
                          >
                            <CheckSquare size={12} /> {session.card_id}
                          </button>
                        )}

                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                          • Componente: <strong>{session.component || 'General'}</strong>
                        </span>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        {/* Enlace a Rama en Grafo */}
                        {session.agreed_branch && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedBranch(session.agreed_branch);
                              setSubTab('graph');
                            }}
                            title="Ver rama acordada en el Grafo de Commits"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              fontSize: '11px',
                              fontFamily: 'var(--font-mono)',
                              padding: '2px 8px',
                              borderRadius: '6px',
                              border: '1px solid var(--border-subtle)',
                              backgroundColor: 'var(--bg-app)',
                              color: 'var(--accent-primary)',
                              cursor: 'pointer'
                            }}
                          >
                            <GitBranch size={11} /> {session.agreed_branch} →
                          </button>
                        )}

                        <span style={{
                          fontSize: '11px',
                          fontWeight: 700,
                          padding: '3px 10px',
                          borderRadius: '4px',
                          backgroundColor: isAccepted ? 'rgba(var(--accent-primary-rgb), 0.15)' : isCompromise ? 'rgba(245, 158, 11, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                          color: isAccepted ? 'var(--accent-success)' : isCompromise ? 'var(--accent-warning)' : '#ef4444'
                        }}>
                          {verdict || 'EN CURSO'}
                        </span>
                      </div>
                    </div>

                    {/* Desglose de los 3 Turnos */}
                    {isExpanded && (
                      <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
                        {session.turns.map((turn, tIdx) => (
                          <div
                            key={tIdx}
                            style={{
                              padding: '12px 14px',
                              borderRadius: 'var(--radius-md)',
                              border: '1px solid var(--border-subtle)',
                              backgroundColor: 'var(--bg-app)',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: '6px'
                            }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px' }}>
                              <span style={{ fontWeight: 700, color: 'var(--accent-primary)' }}>
                                Turno {turn.turn || tIdx + 1}: {turn.stage}
                              </span>
                              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', fontSize: '11px' }}>
                                Emitido por: {turn.sender_node}
                              </span>
                            </div>

                            {/* Contenido legible del Turno */}
                            {turn.intent && (
                              <div style={{ fontSize: '12px', color: 'var(--text-primary)' }}>
                                <strong>Propuesta:</strong> {turn.intent}
                              </div>
                            )}
                            {turn.rationale && (
                              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                                <strong>Justificación (OKF):</strong> {turn.rationale}
                              </div>
                            )}
                            {turn.critique && (
                              <div style={{ fontSize: '12px', color: 'var(--accent-warning)' }}>
                                <strong>Crítica Técnica:</strong> {turn.critique}
                              </div>
                            )}
                            {turn.synthesis_notes && (
                              <div style={{ fontSize: '12px', color: 'var(--accent-success)' }}>
                                <strong>Síntesis Final:</strong> {turn.synthesis_notes}
                              </div>
                            )}
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
      )}

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
              <h3 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
                Crear Nueva Rama en {repoTarget === 'app' ? 'App' : 'Gobernanza'}
              </h3>
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

      {/* Modal para configurar URL remota manual */}
      {isRemoteModalOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '20px'
          }}
          onClick={() => setIsRemoteModalOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              backgroundColor: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-lg)',
              padding: '20px',
              width: '100%',
              maxWidth: '460px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              boxShadow: 'var(--shadow-lg)'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
                Configurar Repositorio Remoto ({repoTarget === 'app' ? 'App' : 'Gobernanza'})
              </h3>
              <button
                onClick={() => setIsRemoteModalOpen(false)}
                style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
              >
                <X size={16} />
              </button>
            </div>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
              Puedes ingresar la URL remota de GitHub para este sub-repositorio o auto-publicarlo directamente con tu GitHub PAT guardado.
            </p>
            <form onSubmit={handleSetRemote} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>
                  URL Remota Git (HTTPS o SSH)
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
                    padding: '8px 12px',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '13px',
                    outline: 'none'
                  }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '6px' }}>
                <button
                  type="button"
                  onClick={() => {
                    setIsRemoteModalOpen(false);
                    handleAutoPublish();
                  }}
                  disabled={actionLoading}
                  style={{
                    padding: '7px 12px',
                    fontSize: '12px',
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: 'rgba(var(--accent-primary-rgb), 0.15)',
                    color: 'var(--accent-primary)',
                    cursor: actionLoading ? 'wait' : 'pointer',
                    fontWeight: 600
                  }}
                >
                  ⚡ Auto-Crear en GitHub
                </button>
                <div style={{ display: 'flex', gap: '8px' }}>
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
                    Guardar Remoto
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
