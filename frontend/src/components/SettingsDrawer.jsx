import React, { useState, useEffect, useCallback } from 'react';
import {
  X,
  Copy,
  Check,
  Terminal,
  Share2,
  Github,
  BookOpen,
  ShieldCheck,
  Key,
  RefreshCw,
  Eye,
  EyeOff,
  LogOut,
  FolderTree,
  FileText,
  FileCode,
  UserCheck,
  FlaskConical,
  Plus,
  Trash2,
  Edit3,
  Lock,
  Unlock,
  Activity,
  Globe,
  Tag,
  Server
} from 'lucide-react';

export function SettingsDrawer({
  isOpen,
  onClose,
  api,
  activeProject,
  projectsList,
  initialTab = 'p2p',
  onGithubStatusChange
}) {
  const [activeTab, setActiveTab] = useState(initialTab);
  const [inviteUrl, setInviteUrl] = useState('');
  const [mcpConfig, setMcpConfig] = useState('');
  const [agentPrompt, setAgentPrompt] = useState('');
  const [githubStatus, setGithubStatus] = useState(null);
  const [systemInfo, setSystemInfo] = useState(null);
  const [copiedLink, setCopiedLink] = useState(false);
  const [copiedConfig, setCopiedConfig] = useState(false);
  const [copiedPrompt, setCopiedPrompt] = useState(false);

  // Estados de Personal Access Token (PAT)
  const [patInput, setPatInput] = useState('');
  const [showPat, setShowPat] = useState(false);
  const [patLoading, setPatLoading] = useState(false);
  const [patMessage, setPatMessage] = useState(null);

  // Estados del Servidor de Señalización WebRTC (Tarea 4 / Tarea 11)
  const [signalingUrlInput, setSignalingUrlInput] = useState('');
  const [signalingUrlLoading, setSignalingUrlLoading] = useState(false);
  const [signalingUrlMessage, setSignalingUrlMessage] = useState(null);

  // Estados del Visor OKF en Crudo
  const [okfTree, setOkfTree] = useState([]);
  const [selectedFilePath, setSelectedFilePath] = useState('');
  const [selectedFileContent, setSelectedFileContent] = useState('');
  const [fileLoading, setFileLoading] = useState(false);
  const [isAsideHidden, setIsAsideHidden] = useState(!isOpen);

  // Estados Multi-Seed P2P (Hito 4.4)
  const [p2pSeeds, setP2pSeeds] = useState([]);
  const [p2pPeers, setP2pPeers] = useState([]);
  const [newSeedInput, setNewSeedInput] = useState('');
  const [seedFeedback, setSeedFeedback] = useState(null);
  const [seedLoading, setSeedLoading] = useState(false);

  // Estados Laboratorio OKF & Puertos (Hito 4.4)
  const [okfFolders, setOkfFolders] = useState([]);
  const [newFolderPath, setNewFolderPath] = useState('');
  const [newFolderCategory, setNewFolderCategory] = useState('custom');
  const [newFolderDesc, setNewFolderDesc] = useState('');
  const [renamingFolderPath, setRenamingFolderPath] = useState(null);
  const [renameTargetInput, setRenameTargetInput] = useState('');
  const [folderFeedback, setFolderFeedback] = useState(null);

  const [tagsItems, setTagsItems] = useState([]);
  const [selectedTagPath, setSelectedTagPath] = useState('');
  const [tagsEditorInput, setTagsEditorInput] = useState('');
  const [tagsFeedback, setTagsFeedback] = useState(null);
  const [labFreshness, setLabFreshness] = useState(null);
  const [freshnessLoading, setFreshnessLoading] = useState(false);

  const [extensionGates, setExtensionGates] = useState([]);
  const [gateDomainInputs, setGateDomainInputs] = useState({});
  const [gatePrivateIpsInputs, setGatePrivateIpsInputs] = useState({});
  const [gateFeedback, setGateFeedback] = useState(null);
  const [restTestUrl, setRestTestUrl] = useState('https://api.github.com');
  const [restTestResult, setRestTestResult] = useState(null);

  const [diagnosticsResult, setDiagnosticsResult] = useState(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);

  const targetProject = activeProject || (projectsList && projectsList[0]);
  const targetProjectUuid = targetProject?.project_uuid;

  useEffect(() => {
    if (isOpen) {
      setIsAsideHidden(false);
    }
  }, [isOpen]);

  // Sincronizar tab si cambia initialTab al abrir
  useEffect(() => {
    if (isOpen && initialTab) {
      setActiveTab(initialTab);
    }
  }, [isOpen, initialTab]);

  const loadP2pSeeds = useCallback(async () => {
    if (!api?.list_p2p_seeds || !targetProjectUuid) return;
    try {
      const res = await api.list_p2p_seeds(targetProjectUuid);
      if (res?.success) {
        setP2pSeeds(res.seeds || []);
        setP2pPeers(res.peers || []);
      }
    } catch { /* noop */ }
  }, [api, targetProjectUuid]);

  const loadOkfLabData = useCallback(async () => {
    if (!api) return;
    const promises = [
      targetProjectUuid ? api.list_okf_folders?.(targetProjectUuid) : Promise.resolve(null),
      targetProjectUuid ? api.get_okf_tags_index?.(targetProjectUuid) : Promise.resolve(null),
      targetProjectUuid ? api.get_okf_freshness_report?.(targetProjectUuid) : Promise.resolve(null),
      api.get_extension_gates_status?.()
    ];
    const [foldersRes, tagsRes, freshRes, gatesRes] = await Promise.allSettled(promises);

    if (foldersRes?.status === 'fulfilled' && foldersRes.value?.success) {
      setOkfFolders(foldersRes.value.folders || []);
    }
    if (tagsRes?.status === 'fulfilled' && tagsRes.value?.success) {
      const items = tagsRes.value.items || [];
      setTagsItems(items);
      if (items.length > 0) {
        setSelectedTagPath(prev => {
          if (prev) return prev;
          setTagsEditorInput(ed => ed || (items[0].tags || []).join(', '));
          return items[0].path;
        });
      }
    }
    if (freshRes?.status === 'fulfilled' && freshRes.value?.success) {
      setLabFreshness(freshRes.value);
    }
    if (gatesRes?.status === 'fulfilled' && gatesRes.value?.success) {
      const gates = gatesRes.value.gates || [];
      setExtensionGates(gates);
      const domainsMap = {};
      const privateMap = {};
      gates.forEach(g => {
        domainsMap[g.connector_id] = Array.isArray(g.allowed_domains) ? g.allowed_domains.join(', ') : '';
        privateMap[g.connector_id] = Boolean(g.allow_private_ips);
      });
      setGateDomainInputs(prev => ({ ...domainsMap, ...prev }));
      setGatePrivateIpsInputs(prev => ({ ...privateMap, ...prev }));
    }
  }, [api, targetProjectUuid]);

  const loadOkfTree = async (isCancelled = () => false) => {
    if (!api || !targetProject) return;
    try {
      const res = await api.get_okf_raw_tree?.(targetProject.project_uuid);
      if (isCancelled()) return;
      if (res?.success && res.tree?.length > 0) {
        setOkfTree(res.tree);
        const first = res.tree[0].path;
        setSelectedFilePath(first);
        loadFile(first);
      }
    } catch (e) {
      console.error("Error al cargar árbol OKF:", e);
    }
  };

  const loadFile = async (path) => {
    if (!api || !targetProject || !path) return;
    setFileLoading(true);
    try {
      const res = await api.read_okf_file?.(targetProject.project_uuid, path);
      if (res?.success) {
        setSelectedFilePath(path);
        setSelectedFileContent(res.content);
      }
    } catch (e) {
      console.error("Error al leer archivo OKF:", e);
    } finally {
      setFileLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen || !api) return;
    let cancelled = false;

    const promises = [
      api.get_system_info?.(),
      api.get_github_status?.(),
      api.get_signaling_server_url?.()
    ];
    if (targetProjectUuid) {
      promises.push(api.generate_p2p_invite?.(targetProjectUuid));
      promises.push(api.get_mcp_config_snippet?.(targetProjectUuid));
    }

    Promise.allSettled(promises).then(results => {
      if (cancelled) return;
      const [sysRes, ghRes, signalingRes, inviteRes, mcpRes] = results;

      if (sysRes?.status === 'fulfilled' && sysRes.value?.success) {
        setSystemInfo(sysRes.value);
      }
      if (ghRes?.status === 'fulfilled' && ghRes.value?.success) {
        const newGh = ghRes.value.github;
        setGithubStatus(prev => {
          if (!prev || prev.authenticated !== newGh.authenticated || prev.username !== newGh.username) {
            if (onGithubStatusChange) onGithubStatusChange(newGh);
          }
          return newGh;
        });
      }
      // get_signaling_server_url() devuelve la URL directamente como string (no un dict
      // {success, ...}), a diferencia de la mayoría de los métodos del puente -- ver
      // api_bridge.py:246.
      if (signalingRes?.status === 'fulfilled' && typeof signalingRes.value === 'string') {
        setSignalingUrlInput(signalingRes.value);
      }
      if (inviteRes?.status === 'fulfilled' && inviteRes.value?.success) {
        setInviteUrl(inviteRes.value.invite_url || '');
      }
      if (mcpRes?.status === 'fulfilled' && mcpRes.value?.success) {
        setMcpConfig(mcpRes.value.mcp_json);
        setAgentPrompt(mcpRes.value.agent_seed_prompt);
      }
    });

    loadP2pSeeds();

    return () => { cancelled = true; };
  }, [isOpen, api, targetProjectUuid, onGithubStatusChange, loadP2pSeeds]);

  // Carga perezosa del arbol OKF SOLO cuando la pestaña okf_viewer este activa
  useEffect(() => {
    if (!isOpen || !api || !targetProjectUuid || activeTab !== 'okf_viewer') return;
    if (okfTree && okfTree.length > 0) return;
    let cancelled = false;
    loadOkfTree(() => cancelled);
    return () => { cancelled = true; };
  }, [isOpen, api, targetProjectUuid, activeTab]);

  // Carga perezosa de Laboratorio OKF & Puertos cuando la pestaña okf_lab esté activa
  useEffect(() => {
    if (!isOpen || !api || activeTab !== 'okf_lab') return;
    loadOkfLabData();
  }, [isOpen, api, activeTab, loadOkfLabData]);

  const copyToClipboard = (text, setter) => {
    navigator.clipboard.writeText(text);
    setter(true);
    setTimeout(() => setter(false), 2000);
  };

  const handleSavePat = async () => {
    if (!api || !patInput.trim()) return;
    setPatLoading(true);
    setPatMessage(null);
    try {
      const res = await api.save_github_pat(patInput.trim());
      if (res.success) {
        setPatInput('');
        setPatMessage({ type: 'success', text: `¡Autenticado exitosamente vía PAT como @${res.username}!` });
        const st = await api.get_github_status();
        if (st.success) {
          setGithubStatus(st.github);
          if (onGithubStatusChange) onGithubStatusChange(st.github);
        }
      } else {
        setPatMessage({ type: 'error', text: res.error || 'Error al validar token de GitHub' });
      }
    } catch (e) {
      setPatMessage({ type: 'error', text: 'Error de comunicación: ' + e });
    } finally {
      setPatLoading(false);
    }
  };

  const handleClearPat = async () => {
    if (!api) return;
    setPatLoading(true);
    setPatMessage(null);
    try {
      const res = await api.clear_github_pat();
      if (res.success) {
        setPatMessage({ type: 'success', text: 'Token PAT eliminado. Sesión cerrada.' });
        const st = await api.get_github_status();
        if (st.success) {
          setGithubStatus(st.github);
          if (onGithubStatusChange) onGithubStatusChange(st.github);
        }
      } else {
        setPatMessage({ type: 'error', text: res.error || 'Error al eliminar token' });
      }
    } catch (e) {
      setPatMessage({ type: 'error', text: 'Error: ' + e });
    } finally {
      setPatLoading(false);
    }
  };

  const handleSaveSignalingUrl = async () => {
    if (!api) return;
    setSignalingUrlLoading(true);
    setSignalingUrlMessage(null);
    try {
      const res = await api.set_signaling_server_url(signalingUrlInput.trim());
      if (res.success) {
        setSignalingUrlMessage({ type: 'success', text: 'Servidor de señalización P2P guardado. Reinicia la aplicación para aplicarlo.' });
      } else {
        setSignalingUrlMessage({ type: 'error', text: res.error || 'Error al guardar la URL del servidor de señalización' });
      }
    } catch (e) {
      setSignalingUrlMessage({ type: 'error', text: 'Error de comunicación: ' + e });
    } finally {
      setSignalingUrlLoading(false);
    }
  };

  const handleRefreshGithub = async () => {
    if (!api) return;
    setPatLoading(true);
    setPatMessage(null);
    try {
      const st = await api.get_github_status();
      if (st.success) {
        setGithubStatus(st.github);
        if (onGithubStatusChange) onGithubStatusChange(st.github);
        setPatMessage({ type: 'info', text: 'Estado de GitHub actualizado.' });
      }
    } catch (e) {
      setPatMessage({ type: 'error', text: 'Error al comprobar GitHub: ' + e });
    } finally {
      setPatLoading(false);
    }
  };

  // --- Handlers Multi-Seed P2P (Hito 4.4) ---
  const handleAddSeed = async () => {
    const seedUrl = newSeedInput.trim();
    if (!api?.add_p2p_seed || !targetProjectUuid || !seedUrl) return;
    setSeedLoading(true);
    setSeedFeedback(null);
    try {
      const res = await api.add_p2p_seed(targetProjectUuid, seedUrl);
      if (res?.success) {
        setP2pSeeds(res.seeds || []);
        setNewSeedInput('');
        setSeedFeedback({ type: 'success', text: `Seed agregado: ${seedUrl}` });
      } else {
        setSeedFeedback({ type: 'error', text: res?.error || 'No se pudo agregar el seed' });
      }
    } catch (e) {
      setSeedFeedback({ type: 'error', text: 'Error al agregar seed: ' + e });
    } finally {
      setSeedLoading(false);
    }
  };

  const handleRemoveSeed = async (seedUrl) => {
    if (!api?.remove_p2p_seed || !targetProjectUuid) return;
    setSeedLoading(true);
    setSeedFeedback(null);
    try {
      const res = await api.remove_p2p_seed(targetProjectUuid, seedUrl);
      if (res?.success) {
        setP2pSeeds(res.seeds || []);
        setSeedFeedback({ type: 'info', text: `Seed eliminado: ${seedUrl}` });
      } else {
        setSeedFeedback({ type: 'error', text: res?.error || 'No se pudo eliminar el seed' });
      }
    } catch (e) {
      setSeedFeedback({ type: 'error', text: 'Error al eliminar seed: ' + e });
    } finally {
      setSeedLoading(false);
    }
  };

  // --- Handlers Laboratorio OKF & Puertos (Hito 4.4) ---
  const handleAddOkfFolder = async () => {
    const folderPath = newFolderPath.trim();
    if (!api?.add_okf_folder || !targetProjectUuid || !folderPath) return;
    setFolderFeedback(null);
    try {
      const res = await api.add_okf_folder(targetProjectUuid, folderPath, newFolderCategory || 'custom', newFolderDesc.trim());
      if (res?.success) {
        setOkfFolders(res.folders || []);
        setNewFolderPath('');
        setNewFolderDesc('');
        setFolderFeedback({ type: 'success', text: `Carpeta OKF agregada: ${folderPath}` });
      } else {
        setFolderFeedback({ type: 'error', text: res?.error || 'No se pudo agregar la carpeta' });
      }
    } catch (e) {
      setFolderFeedback({ type: 'error', text: 'Error: ' + e });
    }
  };

  const handleRenameOkfFolder = async (oldPath) => {
    const newPath = renameTargetInput.trim();
    if (!api?.rename_okf_folder || !targetProjectUuid || !newPath || newPath === oldPath) {
      setRenamingFolderPath(null);
      return;
    }
    setFolderFeedback(null);
    try {
      const res = await api.rename_okf_folder(targetProjectUuid, oldPath, newPath);
      if (res?.success) {
        setOkfFolders(res.folders || []);
        setRenamingFolderPath(null);
        setRenameTargetInput('');
        setFolderFeedback({ type: 'success', text: `Carpeta renombrada a ${newPath}` });
      } else {
        setFolderFeedback({ type: 'error', text: res?.error || 'Error al renombrar carpeta' });
      }
    } catch (e) {
      setFolderFeedback({ type: 'error', text: 'Error: ' + e });
    }
  };

  const handleRemoveOkfFolder = async (folderPath) => {
    if (!api?.remove_okf_folder || !targetProjectUuid) return;
    setFolderFeedback(null);
    try {
      const res = await api.remove_okf_folder(targetProjectUuid, folderPath, true);
      if (res?.success) {
        setOkfFolders(res.folders || []);
        setFolderFeedback({ type: 'info', text: `Carpeta archivada/eliminada: ${folderPath}` });
      } else {
        setFolderFeedback({ type: 'error', text: res?.error || 'Error al eliminar carpeta' });
      }
    } catch (e) {
      setFolderFeedback({ type: 'error', text: 'Error: ' + e });
    }
  };

  const handleSaveArtifactTags = async () => {
    if (!api?.update_artifact_tags || !targetProjectUuid || !selectedTagPath.trim()) return;
    setTagsFeedback(null);
    const parsedTags = tagsEditorInput
      .split(',')
      .map(t => t.trim())
      .filter(Boolean);
    try {
      const res = await api.update_artifact_tags(targetProjectUuid, selectedTagPath.trim(), parsedTags);
      if (res?.success) {
        if (res.index?.items) {
          setTagsItems(res.index.items);
        } else {
          setTagsItems(prev => {
            const exists = prev.some(i => i.path === selectedTagPath.trim());
            if (!exists) return [...prev, { path: selectedTagPath.trim(), tags: res.tags || parsedTags }];
            return prev.map(i => (i.path === selectedTagPath.trim() ? { ...i, tags: res.tags || parsedTags } : i));
          });
        }
        setTagsFeedback({ type: 'success', text: `Tags YAML actualizados en ${res.path || selectedTagPath.trim()}` });
      } else {
        setTagsFeedback({ type: 'error', text: res?.error || 'No se pudieron guardar los tags' });
      }
    } catch (e) {
      setTagsFeedback({ type: 'error', text: 'Error: ' + e });
    }
  };

  const handleRunFreshnessReport = async () => {
    if (!api?.get_okf_freshness_report || !targetProjectUuid) return;
    setFreshnessLoading(true);
    try {
      const res = await api.get_okf_freshness_report(targetProjectUuid);
      if (res?.success) {
        setLabFreshness(res);
      }
    } catch (e) {
      console.error('Error ejecutando Freshness:', e);
    } finally {
      setFreshnessLoading(false);
    }
  };

  const handleUnlockGate = async (connectorId) => {
    if (!api?.unlock_extension_gate) return;
    setGateFeedback(null);
    const domainsRaw = gateDomainInputs[connectorId] || '';
    const allowedDomains = domainsRaw.split(',').map(d => d.trim()).filter(Boolean);
    const allowPrivate = Boolean(gatePrivateIpsInputs[connectorId]);
    try {
      const res = await api.unlock_extension_gate(connectorId, allowedDomains, allowPrivate);
      if (res?.success && res.gate) {
        setExtensionGates(prev => {
          const exists = prev.some(g => g.connector_id === connectorId);
          return exists
            ? prev.map(g => (g.connector_id === connectorId ? res.gate : g))
            : [...prev, res.gate];
        });
        setGateFeedback({ type: 'success', text: `Puerta '${connectorId}' desbloqueada con firma Ed25519.` });
      } else {
        setGateFeedback({ type: 'error', text: res?.error || 'Error al desbloquear puerta' });
      }
    } catch (e) {
      setGateFeedback({ type: 'error', text: 'Error: ' + e });
    }
  };

  const handleLockGate = async (connectorId) => {
    if (!api?.lock_extension_gate) return;
    setGateFeedback(null);
    try {
      const res = await api.lock_extension_gate(connectorId);
      if (res?.success && res.gate) {
        setExtensionGates(prev => prev.map(g => (g.connector_id === connectorId ? res.gate : g)));
        setGateFeedback({ type: 'info', text: `Puerta '${connectorId}' cerrada.` });
      } else {
        setGateFeedback({ type: 'error', text: res?.error || 'Error al cerrar puerta' });
      }
    } catch (e) {
      setGateFeedback({ type: 'error', text: 'Error: ' + e });
    }
  };

  const handleTestRestGate = async () => {
    if (!api?.test_outbound_rest_gate || !restTestUrl.trim()) return;
    setRestTestResult(null);
    try {
      const res = await api.test_outbound_rest_gate('outbound_rest', restTestUrl.trim());
      setRestTestResult(res);
    } catch (e) {
      setRestTestResult({ success: false, blocked_reason: String(e) });
    }
  };

  const handleRunDiagnostics = async () => {
    if (!api?.run_system_diagnostics) return;
    setDiagnosticsLoading(true);
    try {
      const res = await api.run_system_diagnostics(targetProjectUuid || '');
      setDiagnosticsResult(res);
    } catch (e) {
      setDiagnosticsResult({ success: false, checks: [{ name: 'Bridge RPC', status: 'failed', detail: String(e) }], summary: { passed: 0, failed: 1 } });
    } finally {
      setDiagnosticsLoading(false);
    }
  };

  return (
    <>
      {/* Backdrop (FLK-03: sin blur, animado por opacidad, siempre montado) */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.45)',
          opacity: isOpen ? 1 : 0,
          pointerEvents: isOpen ? 'auto' : 'none',
          transition: 'opacity 0.2s ease',
          zIndex: 90
        }}
      />

      {/* Drawer Container (Totalmente Responsive) */}
      <aside
        onTransitionEnd={() => {
          if (!isOpen) setIsAsideHidden(true);
        }}
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          width: 'clamp(340px, 52vw, 760px)',
          maxWidth: '96vw',
          height: '100vh',
          backgroundColor: 'var(--bg-surface)',
          borderLeft: '1px solid var(--border-subtle)',
          boxShadow: 'var(--shadow-lg)',
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
          willChange: 'transform',
          contain: 'layout paint style',
          visibility: (!isOpen && isAsideHidden) ? 'hidden' : 'visible',
          display: 'flex',
          flexDirection: 'column',
          zIndex: 100
        }}
      >
        {/* Header */}
        <div style={{
          padding: '16px 20px',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: 'var(--bg-surface)'
        }}>
          <div>
            <h3 style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>⚙ Configuración del Nodo</h3>
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
              {systemInfo ? systemInfo.active_node : 'Cargando nodo...'}
            </span>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '6px',
              borderRadius: 'var(--radius-sm)'
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Tab Navigation */}
        <div style={{
          display: 'flex',
          overflowX: 'auto',
          borderBottom: '1px solid var(--border-subtle)',
          backgroundColor: 'var(--bg-subtle)',
          padding: '4px 8px 0 8px',
          gap: '2px'
        }}>
          {[
            { id: 'okf_viewer', label: 'Visor OKF', icon: FolderTree },
            { id: 'okf_lab', label: 'Laboratorio OKF & Puertos', icon: FlaskConical },
            { id: 'p2p', label: 'Enlace P2P', icon: Share2 },
            { id: 'mcp', label: 'Agentes MCP', icon: Terminal },
            { id: 'github', label: 'GitHub Auth', icon: Github },
            { id: 'manual', label: 'Atajos & Manual', icon: BookOpen }
          ].map(tab => {
            const Icon = tab.icon;
            const isSelected = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '8px 12px',
                  fontSize: '12px',
                  fontWeight: isSelected ? 600 : 500,
                  border: 'none',
                  borderBottom: isSelected ? '2px solid var(--accent-primary)' : '2px solid transparent',
                  backgroundColor: 'transparent',
                  color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  transition: 'var(--transition)'
                }}
              >
                <Icon size={14} color={isSelected ? 'var(--accent-primary)' : 'currentColor'} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Content Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '18px 20px' }}>

          {/* TAB 0: Visor OKF en Crudo */}
          {activeTab === 'okf_viewer' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '14px' }}>
              <div>
                <h4 style={{ fontSize: '13px', fontWeight: 600, margin: '0 0 4px 0' }}>Árbol de Gobernanza OKF en Crudo</h4>
                <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0 }}>
                  Explora y visualiza directamente los archivos Markdown y esquemas JSON del repositorio <code>docs/okf/</code>.
                </p>
              </div>

              {/* Split Viewer Container */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(180px, 1fr) 2fr',
                gap: '12px',
                minHeight: '420px',
                height: 'calc(100vh - 220px)'
              }}>
                {/* File Tree Left Pane */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '10px',
                  overflowY: 'auto',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px'
                }}>
                  <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-secondary)', padding: '4px 6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <FolderTree size={14} /> docs/okf/
                  </div>

                  {okfTree.map(file => {
                    const isSelected = selectedFilePath === file.path;
                    const isJson = file.name.endsWith('.json');
                    const FileIcon = isJson ? FileCode : FileText;

                    return (
                      <button
                        key={file.path}
                        onClick={() => loadFile(file.path)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          padding: '6px 8px',
                          fontSize: '11px',
                          fontFamily: 'var(--font-mono)',
                          borderRadius: 'var(--radius-sm)',
                          border: 'none',
                          backgroundColor: isSelected ? 'var(--bg-subtle)' : 'transparent',
                          color: isSelected ? 'var(--accent-primary)' : 'var(--text-primary)',
                          fontWeight: isSelected ? 600 : 400,
                          textAlign: 'left',
                          cursor: 'pointer',
                          transition: 'var(--transition)'
                        }}
                      >
                        <FileIcon size={12} color={isSelected ? 'var(--accent-primary)' : 'var(--text-secondary)'} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {file.path}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* File Content Right Pane */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  display: 'flex',
                  flexDirection: 'column',
                  overflow: 'hidden'
                }}>
                  {/* File Header Toolbar */}
                  <div style={{
                    padding: '8px 12px',
                    borderBottom: '1px solid var(--border-subtle)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    backgroundColor: 'var(--bg-subtle)'
                  }}>
                    <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {selectedFilePath || 'Selecciona un archivo'}
                    </span>

                    <div style={{ display: 'flex', gap: '4px' }}>
                      <button
                        onClick={() => copyToClipboard(selectedFileContent, setCopiedConfig)}
                        title="Copiar contenido"
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: 'var(--text-secondary)',
                          cursor: 'pointer',
                          padding: '3px 6px',
                          fontSize: '11px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px'
                        }}
                      >
                        {copiedConfig ? <Check size={12} color="var(--accent-success)" /> : <Copy size={12} />}
                      </button>
                    </div>
                  </div>

                  {/* File Body */}
                  <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
                    {fileLoading ? (
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Cargando contenido...</div>
                    ) : (
                      <pre style={{
                        margin: 0,
                        fontSize: '11px',
                        fontFamily: 'var(--font-mono)',
                        lineHeight: 1.5,
                        color: 'var(--text-primary)',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word'
                      }}>
                        {selectedFileContent}
                      </pre>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 1: Enlace P2P */}
          {activeTab === 'p2p' && (
            <div>
              <h4 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>Enlace P2P Criptográfico</h4>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
                Comparte este enlace para conectar otro desarrollador o nodo a este proyecto mediante protocolo P2P cifrado con Ed25519.
              </p>

              {targetProject ? (
                <div>
                  <div style={{
                    fontSize: '12px',
                    fontWeight: 600,
                    marginBottom: '6px',
                    color: 'var(--text-primary)'
                  }}>
                    Proyecto Activo: {targetProject.name}
                  </div>
                  <div style={{
                    backgroundColor: 'var(--bg-card)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    padding: '12px',
                    marginBottom: '10px'
                  }}>
                    <input
                      type="text"
                      readOnly
                      value={inviteUrl}
                      style={{
                        width: '100%',
                        backgroundColor: 'transparent',
                        border: 'none',
                        fontFamily: 'var(--font-mono)',
                        fontSize: '11px',
                        color: 'var(--accent-primary)',
                        outline: 'none'
                      }}
                    />
                  </div>

                  <button
                    onClick={() => copyToClipboard(inviteUrl, setCopiedLink)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '8px',
                      width: '100%',
                      padding: '10px',
                      borderRadius: 'var(--radius-md)',
                      border: 'none',
                      backgroundColor: copiedLink ? 'var(--accent-success)' : 'var(--accent-primary)',
                      color: '#ffffff',
                      fontWeight: 600,
                      fontSize: '13px',
                      cursor: 'pointer',
                      transition: 'var(--transition)'
                    }}
                  >
                    {copiedLink ? <Check size={16} /> : <Copy size={16} />}
                    {copiedLink ? '¡Enlace P2P Copiado!' : 'Copiar Enlace P2P'}
                  </button>

                  <div style={{
                    marginTop: '16px',
                    padding: '12px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--bg-subtle)',
                    fontSize: '11px',
                    color: 'var(--text-secondary)',
                    lineHeight: 1.5,
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '8px'
                  }}>
                    <ShieldCheck size={16} style={{ flexShrink: 0, marginTop: '2px', color: 'var(--accent-primary)' }} />
                    <div>
                      <strong>Aprobación de Colaboradores (HITL):</strong> Cuando el invitado ingrese el enlace, entrará en estado pendiente hasta que apruebes su identidad en la pestaña <em>Colaboradores P2P</em>.
                    </div>
                  </div>
                </div>
              ) : (
                <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
                  Selecciona un proyecto para generar un enlace de invitación P2P.
                </div>
              )}

              {/* Servidor de señalización WebRTC (Tarea 4 / Tarea 11) */}
              <div style={{
                marginTop: '20px',
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>
                  Servidor de Señalización P2P{' '}
                  <span style={{
                    fontSize: '10px',
                    fontWeight: 700,
                    fontFamily: 'var(--font-mono)',
                    color: 'var(--accent-warning)',
                    backgroundColor: 'rgba(245, 158, 11, 0.12)',
                    padding: '1px 6px',
                    borderRadius: '3px',
                    marginLeft: '4px'
                  }}>
                    EXPERIMENTAL
                  </span>
                </h5>
                <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.4 }}>
                  URL del servidor de señalización WebRTC (<code>ws://</code> o <code>wss://</code>) usado para establecer
                  conexiones P2P directas entre nodos. Déjalo vacío para usar el adaptador local sin señalización remota.
                </p>
                <p
                  data-testid="signaling-experimental-note"
                  style={{ fontSize: '11px', color: 'var(--accent-warning)', margin: 0, lineHeight: 1.4 }}
                >
                  Experimental: la conexión en vivo todavía no está completa, así que configurar un servidor no garantiza
                  que funcione. Las solicitudes de unión siguen dependiendo del código de solicitud que se comparte
                  manualmente. Este valor solo se lee al iniciar la aplicación: reiníciala para que un cambio tenga efecto.
                </p>

                {signalingUrlMessage && (
                  <div style={{
                    padding: '8px 10px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '11px',
                    backgroundColor: signalingUrlMessage.type === 'success' ? 'rgba(var(--accent-primary-rgb), 0.15)' : 'rgba(239, 68, 68, 0.15)',
                    color: signalingUrlMessage.type === 'success' ? 'var(--accent-success)' : 'var(--accent-danger)',
                    border: `1px solid ${signalingUrlMessage.type === 'success' ? 'rgba(var(--accent-primary-rgb), 0.3)' : 'rgba(239, 68, 68, 0.3)'}`
                  }}>
                    {signalingUrlMessage.text}
                  </div>
                )}

                <div style={{ display: 'flex', gap: '6px' }}>
                  <input
                    type="text"
                    placeholder="wss://signal.example.com"
                    value={signalingUrlInput}
                    onChange={e => setSignalingUrlInput(e.target.value)}
                    style={{
                      flex: 1,
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
                    onClick={handleSaveSignalingUrl}
                    disabled={signalingUrlLoading}
                    style={{
                      padding: '8px 14px',
                      fontSize: '12px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: 'var(--accent-primary)',
                      color: '#ffffff',
                      cursor: signalingUrlLoading ? 'not-allowed' : 'pointer',
                      opacity: signalingUrlLoading ? 0.6 : 1,
                      whiteSpace: 'nowrap'
                    }}
                  >
                    {signalingUrlLoading ? 'Guardando...' : 'Guardar'}
                  </button>
                </div>
              </div>

              {/* Gestión de Múltiples Seeds P2P y Pares Conectados (Hito 4.4) */}
              <div
                data-testid="p2p-multi-seed-section"
                style={{
                  marginTop: '16px',
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '16px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                  <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <Server size={14} color="var(--accent-primary)" /> Múltiples Seeds P2P &amp; Pares Conectados
                  </h5>
                  <button
                    type="button"
                    onClick={loadP2pSeeds}
                    title="Refrescar seeds y pares"
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '4px 7px',
                      color: 'var(--text-secondary)',
                      cursor: 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px'
                    }}
                  >
                    <RefreshCw size={12} /> Refrescar
                  </button>
                </div>

                {seedFeedback && (
                  <div style={{
                    padding: '7px 10px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '11px',
                    backgroundColor: seedFeedback.type === 'error' ? 'rgba(239, 68, 68, 0.14)' : 'rgba(var(--accent-primary-rgb), 0.14)',
                    color: seedFeedback.type === 'error' ? 'var(--accent-danger)' : 'var(--accent-success)'
                  }}>
                    {seedFeedback.text}
                  </div>
                )}

                <div style={{ display: 'flex', gap: '6px' }}>
                  <input
                    type="text"
                    data-testid="p2p-seed-input"
                    placeholder="wss://seed-2.example.com:8765"
                    value={newSeedInput}
                    onChange={(e) => setNewSeedInput(e.target.value)}
                    style={{
                      flex: 1,
                      padding: '7px 10px',
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
                    type="button"
                    data-testid="add-p2p-seed-btn"
                    onClick={handleAddSeed}
                    disabled={seedLoading || !newSeedInput.trim() || !targetProjectUuid}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      padding: '7px 12px',
                      fontSize: '12px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: 'var(--accent-primary)',
                      color: '#ffffff',
                      cursor: seedLoading || !newSeedInput.trim() ? 'not-allowed' : 'pointer',
                      opacity: seedLoading || !newSeedInput.trim() ? 0.6 : 1,
                      whiteSpace: 'nowrap'
                    }}
                  >
                    <Plus size={13} /> Agregar Seed
                  </button>
                </div>

                {/* Lista de Seeds */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                    Seeds Configurados ({p2pSeeds.length})
                  </div>
                  {p2pSeeds.length === 0 ? (
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      No hay seeds adicionales registrados para este proyecto.
                    </div>
                  ) : (
                    p2pSeeds.map((seedItem, idx) => {
                      const seedUrl = typeof seedItem === 'string' ? seedItem : (seedItem?.url || seedItem?.seed_url || JSON.stringify(seedItem));
                      return (
                        <div
                          key={`${seedUrl}-${idx}`}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: '8px',
                            padding: '6px 10px',
                            borderRadius: 'var(--radius-sm)',
                            backgroundColor: 'var(--bg-subtle)',
                            fontSize: '11.5px',
                            fontFamily: 'var(--font-mono)'
                          }}
                        >
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{seedUrl}</span>
                          <button
                            type="button"
                            aria-label={`Eliminar seed ${seedUrl}`}
                            onClick={() => handleRemoveSeed(seedUrl)}
                            disabled={seedLoading}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              padding: '3px 7px',
                              fontSize: '11px',
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid rgba(239, 68, 68, 0.35)',
                              backgroundColor: 'transparent',
                              color: 'var(--accent-danger)',
                              cursor: 'pointer'
                            }}
                          >
                            <Trash2 size={11} /> Eliminar
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>

                {/* Lista de Pares Conectados */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', borderTop: '1px solid var(--border-subtle)', paddingTop: '8px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                    Pares Conectados ({p2pPeers.length})
                  </div>
                  {p2pPeers.length === 0 ? (
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Sin pares P2P conectados actualmente.</div>
                  ) : (
                    p2pPeers.map((peer, idx) => {
                      const peerLabel = typeof peer === 'string'
                        ? peer
                        : (peer.node_uuid || peer.peer_id || peer.url || peer.address || `peer-${idx}`);
                      const peerStatus = typeof peer === 'object' ? (peer.status || peer.state || 'connected') : 'connected';
                      return (
                        <div
                          key={`${peerLabel}-${idx}`}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '5px 9px',
                            borderRadius: 'var(--radius-sm)',
                            backgroundColor: 'var(--bg-subtle)',
                            fontSize: '11px',
                            fontFamily: 'var(--font-mono)'
                          }}
                        >
                          <span>{peerLabel}</span>
                          <span className="mcp-badge-fresh">{peerStatus}</span>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: Agente MCP */}
          {activeTab === 'mcp' && (
            <div>
              <h4 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>Conexión con Agentes (Model Context Protocol)</h4>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
                Conecta tu entorno de desarrollo (Cursor, Claude Desktop, Antigravity) al servidor MCP local para permitir a los agentes colaborar a 0 tokens.
              </p>

              {/* Snippet mcp.json */}
              <div style={{ marginBottom: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <span style={{ fontSize: '12px', fontWeight: 600 }}>1. Configuración JSON para mcpServers</span>
                  <button
                    onClick={() => copyToClipboard(mcpConfig, setCopiedConfig)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: copiedConfig ? 'var(--accent-success)' : 'var(--accent-primary)',
                      cursor: 'pointer',
                      fontSize: '11px',
                      fontWeight: 600,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px'
                    }}
                  >
                    {copiedConfig ? <Check size={13} /> : <Copy size={13} />}
                    {copiedConfig ? 'Copiado' : 'Copiar JSON'}
                  </button>
                </div>
                <pre style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px',
                  fontSize: '11px',
                  fontFamily: 'var(--font-mono)',
                  overflowX: 'auto',
                  color: 'var(--text-primary)'
                }}>
                  {mcpConfig}
                </pre>
              </div>

              {/* Prompt Semilla para Agente */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <span style={{ fontSize: '12px', fontWeight: 600 }}>2. Prompt Semilla para el Agente</span>
                  <button
                    onClick={() => copyToClipboard(agentPrompt, setCopiedPrompt)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: copiedPrompt ? 'var(--accent-success)' : 'var(--accent-primary)',
                      cursor: 'pointer',
                      fontSize: '11px',
                      fontWeight: 600,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px'
                    }}
                  >
                    {copiedPrompt ? <Check size={13} /> : <Copy size={13} />}
                    {copiedPrompt ? 'Copiado' : 'Copiar Prompt'}
                  </button>
                </div>
                <textarea
                  readOnly
                  value={agentPrompt}
                  rows={6}
                  style={{
                    width: '100%',
                    backgroundColor: 'var(--bg-card)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    padding: '10px',
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    color: 'var(--text-primary)',
                    outline: 'none',
                    resize: 'none'
                  }}
                />
              </div>
            </div>
          )}

          {/* TAB 3: GitHub (Dual Auth: PAT y gh CLI + Git Identity) */}
          {activeTab === 'github' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div>
                <h4 style={{ fontSize: '13px', fontWeight: 600, margin: '0 0 6px 0' }}>Autenticación Dual de GitHub</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
                  Conecta tu identidad de GitHub mediante <strong>Personal Access Token (PAT)</strong> o <strong>GitHub CLI (gh)</strong> según tus preferencias de desarrollo.
                </p>
              </div>

              {/* Identidad Git Global Detectada (GitHub Desktop Friendly) */}
              {githubStatus?.git_identity?.name && (
                <div style={{
                  padding: '10px 14px',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: 'rgba(var(--accent-primary-rgb), 0.08)',
                  border: '1px solid rgba(var(--accent-primary-rgb), 0.25)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px'
                }}>
                  <UserCheck size={18} color="var(--accent-primary)" style={{ flexShrink: 0 }} />
                  <div style={{ fontSize: '12px' }}>
                    <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                      Identidad Git Local: {githubStatus.git_identity.name}
                    </div>
                    <div style={{ color: 'var(--text-muted)', fontSize: '11px', fontFamily: 'var(--font-mono)' }}>
                      {githubStatus.git_identity.email} • Detectado desde Git Config / GitHub Desktop
                    </div>
                  </div>
                </div>
              )}

              {/* Estado Actual */}
              <div style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '14px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Estado de Sesión:</span>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: githubStatus?.authenticated ? 'rgba(var(--accent-primary-rgb), 0.15)' : 'rgba(239, 68, 68, 0.15)',
                    color: githubStatus?.authenticated ? 'var(--accent-success)' : 'var(--accent-danger)'
                  }}>
                    {githubStatus?.authenticated ? 'Sesión Activa' : 'No Conectado'}
                  </span>
                </div>

                {githubStatus?.authenticated && (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Usuario GitHub:</span>
                      <span style={{ fontSize: '12px', fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--accent-primary)' }}>
                        @{githubStatus.username}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Método Activo:</span>
                      <span style={{
                        fontSize: '11px',
                        fontFamily: 'var(--font-mono)',
                        padding: '1px 6px',
                        borderRadius: '3px',
                        backgroundColor: 'var(--bg-subtle)'
                      }}>
                        {githubStatus.provider === 'pat' ? 'Personal Access Token (PAT)' : githubStatus.provider === 'gh_cli' ? 'GitHub CLI (gh)' : 'Variable Entorno'}
                      </span>
                    </div>
                  </>
                )}

                <div style={{ fontSize: '11px', color: 'var(--text-muted)', borderTop: '1px solid var(--border-subtle)', paddingTop: '6px' }}>
                  {githubStatus?.details}
                </div>
              </div>

              {/* Mensajes de feedback */}
              {patMessage && (
                <div style={{
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '11px',
                  backgroundColor: patMessage.type === 'success' ? 'rgba(var(--accent-primary-rgb), 0.15)' : 'rgba(239, 68, 68, 0.15)',
                  color: patMessage.type === 'success' ? 'var(--accent-success)' : 'var(--accent-danger)',
                  border: `1px solid ${patMessage.type === 'success' ? 'rgba(var(--accent-primary-rgb), 0.3)' : 'rgba(239, 68, 68, 0.3)'}`
                }}>
                  {patMessage.text}
                </div>
              )}

              {/* OPCIÓN 1: Personal Access Token (PAT) */}
              <div style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Key size={16} color="var(--accent-primary)" />
                  <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>Método 1: Personal Access Token (PAT) (Recomendado)</h5>
                </div>

                <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.4 }}>
                  Si usas GitHub Desktop, el método recomendado es el Personal Access Token (con alcance <code>repo</code>), ya que GitHub Desktop no incluye la herramienta CLI en el sistema.
                </p>

                <div style={{ display: 'flex', gap: '6px' }}>
                  <div style={{ position: 'relative', flex: 1 }}>
                    <input
                      type={showPat ? 'text' : 'password'}
                      placeholder="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                      value={patInput}
                      onChange={e => setPatInput(e.target.value)}
                      style={{
                        width: '100%',
                        padding: '8px 34px 8px 10px',
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
                      type="button"
                      onClick={() => setShowPat(prev => !prev)}
                      style={{
                        position: 'absolute',
                        right: '8px',
                        top: '50%',
                        transform: 'translateY(-50%)',
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--text-secondary)',
                        cursor: 'pointer',
                        padding: 0
                      }}
                    >
                      {showPat ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>

                  <button
                    onClick={handleSavePat}
                    disabled={patLoading || !patInput.trim()}
                    style={{
                      padding: '8px 14px',
                      fontSize: '12px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: 'var(--accent-primary)',
                      color: '#ffffff',
                      cursor: patLoading || !patInput.trim() ? 'not-allowed' : 'pointer',
                      opacity: patLoading || !patInput.trim() ? 0.6 : 1,
                      whiteSpace: 'nowrap'
                    }}
                  >
                    {patLoading ? 'Validando...' : 'Guardar Token'}
                  </button>
                </div>

                {githubStatus?.provider === 'pat' && (
                  <button
                    onClick={handleClearPat}
                    disabled={patLoading}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px',
                      padding: '6px 12px',
                      fontSize: '11px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid rgba(239, 68, 68, 0.4)',
                      backgroundColor: 'transparent',
                      color: 'var(--accent-danger)',
                      cursor: 'pointer',
                      marginTop: '4px'
                    }}
                  >
                    <LogOut size={13} /> Desconectar Token PAT
                  </button>
                )}
              </div>

              {/* OPCIÓN 2: GitHub CLI (gh) */}
              <div style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Terminal size={16} color="var(--accent-primary)" />
                  <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>Método 2: GitHub CLI (gh auth login)</h5>
                </div>

                <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.4 }}>
                  Si instalas <code>gh</code> en tu terminal, puedes iniciar sesión automáticamente ejecutando:
                </p>

                <pre style={{
                  margin: 0,
                  padding: '8px 12px',
                  backgroundColor: 'var(--bg-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '11px',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-primary)'
                }}>
                  gh auth login
                </pre>

                <button
                  onClick={handleRefreshGithub}
                  disabled={patLoading}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '6px',
                    padding: '8px 12px',
                    fontSize: '12px',
                    fontWeight: 600,
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    backgroundColor: 'var(--bg-subtle)',
                    color: 'var(--text-primary)',
                    cursor: 'pointer'
                  }}
                >
                  <RefreshCw size={13} /> Comprobar Detección de CLI
                </button>
              </div>
            </div>
          )}

          {/* TAB 4: Manual y Guía del Sistema */}
          {activeTab === 'manual' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              <div>
                <h4 style={{ fontSize: '14px', fontWeight: 700, margin: '0 0 6px 0', color: 'var(--text-primary)' }}>
                  Manual de Operación & Arquitectura de MCP2P
                </h4>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.45 }}>
                  Guía técnica completa para entender y operar los módulos de colaboración local-first, enjambre agéntico y gobernanza P2P.
                </p>
              </div>

              {/* 1. Atajos de Teclado */}
              <div style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '14px 16px'
              }}>
                <h5 style={{ fontSize: '13px', fontWeight: 600, margin: '0 0 10px 0', color: 'var(--accent-primary)' }}>
                  ⌨️ Atajos de Teclado Globales
                </h5>
                <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
                  <tbody>
                    {[
                      ['Ctrl + P', 'Crear Nuevo Proyecto en Hub de Proyectos'],
                      ['Ctrl + N', 'Nueva Tarea Kanban (dentro del proyecto activo)'],
                      ['Ctrl + D', 'Alternar Tema Claro / Oscuro'],
                      ['F1', 'Abrir / Cerrar este Panel de Configuración y Manual'],
                      ['ESC', 'Cerrar cualquier ventana modal o drawer lateral']
                    ].map(([key, desc]) => (
                      <tr key={key} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                        <td style={{ padding: '7px 0', fontFamily: 'var(--font-mono)' }}>
                          <span style={{
                            backgroundColor: 'var(--bg-subtle)',
                            border: '1px solid var(--border-subtle)',
                            padding: '2px 7px',
                            borderRadius: '4px',
                            fontSize: '11px',
                            fontWeight: 600
                          }}>
                            {key}
                          </span>
                        </td>
                        <td style={{ padding: '7px 0', color: 'var(--text-secondary)' }}>{desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 2. Módulos y Conceptos */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0, color: 'var(--text-primary)' }}>
                  📚 Para qué sirve cada módulo
                </h5>

                {/* Hub de Proyectos */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 14px'
                }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    1. Hub de Proyectos (Workspace Hub)
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    Es la pantalla de inicio de MCP2P. Cada proyecto opera con su propio identificador <code>UUIDv4</code> y aislamiento físico estricto de doble repositorio: <code>app/</code> (código fuente) y <code>governance/</code> (documentación OKF y rama de memoria huérfana). Ningún contexto ni tarea se mezcla entre proyectos.
                  </div>
                </div>

                {/* Tablero Kanban & Leases */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 14px'
                }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    2. Tablero Kanban & Criterios de Aceptación (HITL)
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    Permite organizar tareas en 4 columnas (Por Hacer, En Proceso, En Revisión, Fusionado).
                    <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
                      <li><strong>Leases de 30 minutos</strong>: Cuando un agente o tú mueven una tarea a 'En Proceso', se genera un candado atómico que previene que dos agentes trabajen en lo mismo a la vez.</li>
                      <li><strong>Criterios de Aceptación</strong>: Puedes abrir cualquier tarjeta haciendo clic en ella para definir condiciones de éxito y pulsar <em>"Aceptar y Consolidar (HITL)"</em> para validar el resultado.</li>
                    </ul>
                  </div>
                </div>

                {/* Control de Cambios & Grafo Git */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 14px'
                }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    3. Control de Cambios & Grafo Visual de Git
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    Muestra el árbol de ramas y commits dibujado de forma interactiva con carriles de colores (swimlanes), conexiones curvas Bezier y etiquetas de rama. Permite:
                    <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
                      <li><strong>Fetch / Pull</strong>: Descargar y sincronizar cambios del repositorio remoto de GitHub.</li>
                      <li><strong>Publicar Rama</strong>: Empujar tu rama local hacia GitHub (requiere vincular la URL del repositorio remoto).</li>
                      <li><strong>Fusionar en Main</strong>: Integrar ramas de feature a main de forma segura.</li>
                      <li><strong>Vincular Remoto</strong>: Configurar la URL de tu repositorio GitHub origin directamente desde el botón en la barra.</li>
                    </ul>
                  </div>
                </div>

                {/* Consenso Dialéctico 3-Turn A2A & Juicio de Honor */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 14px'
                }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    4. Bandeja HITL, Juicio de Honor Dialéctico (3 Fases) y Matriz Anti-Sycophancy
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    En la pestaña <strong>HITL</strong> puedes inspeccionar cada decisión, tarea en revisión y juicio técnico:
                    <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
                      <li><strong>Fase 1 (Exposición)</strong>: Lectura íntegra de <em>Tesis (Opción A)</em>, <em>Antítesis (Opción B)</em> y <em>Síntesis / Tercera Vía (Opción C)</em>.</li>
                      <li><strong>Fase 2 (Mayéutica Clean-Room)</strong>: Subagentes neutrales evalúan con vectores de foco estocásticos (0 a 4) y temperaturas diferenciadas sin heredar el prompt del usuario. Sus puntuaciones se ocultan antes del voto humano para evitar sesgo cognitivo.</li>
                      <li><strong>Fase 3 (Votación Ponderada 80% + Supremacía Humana)</strong>: El voto humano pesa <code>2.0 pts</code> frente al tope de <code>1.5 × H</code> del pool de subagentes (<code>57.14%</code> humano vs <code>42.86%</code> máquina). Toda aprobación exige <strong>Doble Factor de Confirmación Consciente</strong> y firma <code>Ed25519</code>.</li>
                    </ul>
                  </div>
                </div>

                {/* Colaboradores & Agentes MCP */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 14px'
                }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    5. Colaboradores P2P & Pasarela MCP
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    Permite enlazar nodos de otros desarrolladores sin intermediarios mediante links criptográficos P2P. Además, expone las 10 herramientas del servidor local <code>mcp2p-server</code> sobre transporte stdio/SSE para que agentes de Gemini, Claude o Cursor interactúen en tiempo real con el proyecto.
                  </div>
                </div>

                {/* Visor OKF */}
                <div style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '12px 14px'
                }}>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    6. Visor OKF &amp; Archivos en Crudo
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    Ubicado en la primera pestaña de este drawer lateral. Te permite explorar el árbol de archivos de gobernanza (<code>docs/okf/</code>), leer especificaciones de arquitectura y revisar los registros de decisión de arquitectura (ADRs) sin recargar la interfaz principal.
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 5: Laboratorio OKF & Puertos (Hito 4.4) */}
          {activeTab === 'okf_lab' && (
            <div data-testid="okf-lab-panel" style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
              <div>
                <h4 style={{ fontSize: '14px', fontWeight: 700, margin: '0 0 4px 0', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <FlaskConical size={16} color="var(--accent-primary)" /> Laboratorio OKF &amp; Puertos de Extensión
                </h4>
                <p style={{ fontSize: '11.5px', color: 'var(--text-secondary)', margin: 0 }}>
                  Administra carpetas de conocimiento OKF, etiquetas YAML, algoritmo Freshness, Puertas con Llave Ed25519 y autodiagnóstico en vivo.
                </p>
              </div>

              {/* (a) Gestor de Carpetas OKF */}
              <section
                data-testid="okf-folders-manager"
                style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <FolderTree size={14} color="var(--accent-primary)" /> Gestor de Carpetas OKF ({okfFolders.length})
                  </h5>
                  <button
                    type="button"
                    onClick={loadOkfLabData}
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '3px 7px',
                      color: 'var(--text-secondary)',
                      fontSize: '11px',
                      cursor: 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px'
                    }}
                  >
                    <RefreshCw size={11} /> Sincronizar
                  </button>
                </div>

                {folderFeedback && (
                  <div style={{
                    padding: '6px 10px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '11px',
                    backgroundColor: folderFeedback.type === 'error' ? 'rgba(239, 68, 68, 0.14)' : 'rgba(var(--accent-primary-rgb), 0.14)',
                    color: folderFeedback.type === 'error' ? 'var(--accent-danger)' : 'var(--accent-success)'
                  }}>
                    {folderFeedback.text}
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr 1.2fr auto', gap: '6px' }}>
                  <input
                    type="text"
                    data-testid="okf-new-folder-path"
                    placeholder="docs/okf/runbooks"
                    value={newFolderPath}
                    onChange={(e) => setNewFolderPath(e.target.value)}
                    style={{
                      padding: '6px 8px',
                      fontSize: '11.5px',
                      fontFamily: 'var(--font-mono)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-subtle)',
                      color: 'var(--text-primary)'
                    }}
                  />
                  <select
                    data-testid="okf-new-folder-category"
                    value={newFolderCategory}
                    onChange={(e) => setNewFolderCategory(e.target.value)}
                    className="mcp-combobox"
                    style={{ height: '30px', fontSize: '11.5px' }}
                  >
                    <option value="custom">custom</option>
                    <option value="adr">adr</option>
                    <option value="backend">backend</option>
                    <option value="frontend">frontend</option>
                    <option value="audit">audit</option>
                  </select>
                  <input
                    type="text"
                    data-testid="okf-new-folder-desc"
                    placeholder="Descripción breve"
                    value={newFolderDesc}
                    onChange={(e) => setNewFolderDesc(e.target.value)}
                    style={{
                      padding: '6px 8px',
                      fontSize: '11.5px',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-subtle)',
                      color: 'var(--text-primary)'
                    }}
                  />
                  <button
                    type="button"
                    data-testid="okf-add-folder-btn"
                    onClick={handleAddOkfFolder}
                    disabled={!newFolderPath.trim() || !targetProjectUuid}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      padding: '6px 10px',
                      fontSize: '11.5px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: 'var(--accent-primary)',
                      color: '#ffffff',
                      cursor: newFolderPath.trim() ? 'pointer' : 'not-allowed',
                      opacity: newFolderPath.trim() ? 1 : 0.6
                    }}
                  >
                    <Plus size={12} /> Agregar
                  </button>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {okfFolders.length === 0 ? (
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Sin carpetas OKF listadas.</div>
                  ) : (
                    okfFolders.map(folder => {
                      const isRenaming = renamingFolderPath === folder.path;
                      return (
                        <div
                          key={folder.path}
                          data-testid={`okf-folder-row-${folder.path}`}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            flexWrap: 'wrap',
                            gap: '8px',
                            padding: '7px 10px',
                            borderRadius: 'var(--radius-sm)',
                            backgroundColor: 'var(--bg-subtle)',
                            fontSize: '11.5px'
                          }}
                        >
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0, flex: 1 }}>
                            {isRenaming ? (
                              <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                                <input
                                  type="text"
                                  data-testid={`okf-rename-input-${folder.path}`}
                                  value={renameTargetInput}
                                  onChange={(e) => setRenameTargetInput(e.target.value)}
                                  style={{
                                    padding: '4px 7px',
                                    fontSize: '11px',
                                    fontFamily: 'var(--font-mono)',
                                    borderRadius: 'var(--radius-sm)',
                                    border: '1px solid var(--accent-primary)',
                                    backgroundColor: 'var(--bg-surface)',
                                    color: 'var(--text-primary)'
                                  }}
                                />
                                <button
                                  type="button"
                                  data-testid={`okf-rename-confirm-${folder.path}`}
                                  onClick={() => handleRenameOkfFolder(folder.path)}
                                  style={{
                                    padding: '4px 8px',
                                    fontSize: '11px',
                                    fontWeight: 600,
                                    borderRadius: 'var(--radius-sm)',
                                    border: 'none',
                                    backgroundColor: 'var(--accent-primary)',
                                    color: '#ffffff',
                                    cursor: 'pointer'
                                  }}
                                >
                                  Guardar
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setRenamingFolderPath(null)}
                                  style={{
                                    padding: '4px 8px',
                                    fontSize: '11px',
                                    borderRadius: 'var(--radius-sm)',
                                    border: '1px solid var(--border-subtle)',
                                    backgroundColor: 'transparent',
                                    color: 'var(--text-secondary)',
                                    cursor: 'pointer'
                                  }}
                                >
                                  Cancelar
                                </button>
                              </div>
                            ) : (
                              <>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{folder.path}</span>
                                  <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>({folder.category})</span>
                                  <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--accent-primary)' }}>
                                    {folder.file_count ?? 0} arch.
                                  </span>
                                  {folder.required && (
                                    <span style={{ fontSize: '9.5px', padding: '1px 5px', borderRadius: '3px', backgroundColor: 'rgba(var(--accent-indigo-rgb), 0.16)', color: 'var(--accent-indigo)' }}>
                                      requerida
                                    </span>
                                  )}
                                </div>
                                {folder.description && (
                                  <span style={{ fontSize: '10.5px', color: 'var(--text-secondary)' }}>{folder.description}</span>
                                )}
                              </>
                            )}
                          </div>

                          {!isRenaming && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                              <button
                                type="button"
                                aria-label={`Renombrar ${folder.path}`}
                                onClick={() => {
                                  setRenamingFolderPath(folder.path);
                                  setRenameTargetInput(folder.path);
                                }}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: '3px',
                                  padding: '3px 7px',
                                  fontSize: '11px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid var(--border-subtle)',
                                  backgroundColor: 'var(--bg-surface)',
                                  color: 'var(--text-primary)',
                                  cursor: 'pointer'
                                }}
                              >
                                <Edit3 size={11} /> Renombrar
                              </button>
                              <button
                                type="button"
                                aria-label={`Eliminar ${folder.path}`}
                                disabled={Boolean(folder.required)}
                                onClick={() => handleRemoveOkfFolder(folder.path)}
                                title={folder.required ? 'Carpeta estructural requerida por OKF' : 'Archivar o eliminar carpeta'}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: '3px',
                                  padding: '3px 7px',
                                  fontSize: '11px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid rgba(239, 68, 68, 0.35)',
                                  backgroundColor: 'transparent',
                                  color: 'var(--accent-danger)',
                                  cursor: folder.required ? 'not-allowed' : 'pointer',
                                  opacity: folder.required ? 0.45 : 1
                                }}
                              >
                                <Trash2 size={11} /> Eliminar
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </section>

              {/* (b) Editor de Tags YAML + Ejecutor de Algoritmo Freshness & Firmas */}
              <section
                data-testid="okf-tags-freshness-lab"
                style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                  <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <Tag size={14} color="var(--accent-indigo)" /> Editor de Tags YAML &amp; Algoritmo Freshness
                  </h5>
                  <button
                    type="button"
                    data-testid="run-freshness-btn"
                    onClick={handleRunFreshnessReport}
                    disabled={freshnessLoading || !targetProjectUuid}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      padding: '5px 10px',
                      fontSize: '11.5px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--accent-primary)',
                      backgroundColor: 'rgba(var(--accent-primary-rgb), 0.14)',
                      color: 'var(--accent-primary)',
                      cursor: 'pointer'
                    }}
                  >
                    <Activity size={12} /> {freshnessLoading ? 'Analizando...' : 'Ejecutar Freshness & Firmas'}
                  </button>
                </div>

                {tagsFeedback && (
                  <div style={{
                    padding: '6px 10px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '11px',
                    backgroundColor: tagsFeedback.type === 'error' ? 'rgba(239, 68, 68, 0.14)' : 'rgba(var(--accent-primary-rgb), 0.14)',
                    color: tagsFeedback.type === 'error' ? 'var(--accent-danger)' : 'var(--accent-success)'
                  }}>
                    {tagsFeedback.text}
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr auto', gap: '6px', alignItems: 'center' }}>
                  <input
                    type="text"
                    data-testid="okf-tag-artifact-path"
                    list="okf-tags-artifacts-list"
                    placeholder="docs/okf/adr/ADR-001.md"
                    value={selectedTagPath}
                    onChange={(e) => {
                      const pathVal = e.target.value;
                      setSelectedTagPath(pathVal);
                      const matched = tagsItems.find(it => it.path === pathVal);
                      if (matched) setTagsEditorInput((matched.tags || []).join(', '));
                    }}
                    style={{
                      padding: '6px 8px',
                      fontSize: '11.5px',
                      fontFamily: 'var(--font-mono)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-subtle)',
                      color: 'var(--text-primary)'
                    }}
                  />
                  <datalist id="okf-tags-artifacts-list">
                    {tagsItems.map(it => (
                      <option key={it.path} value={it.path} />
                    ))}
                  </datalist>
                  <input
                    type="text"
                    data-testid="okf-tag-values-input"
                    placeholder="hexagonal, seguridad, p2p"
                    value={tagsEditorInput}
                    onChange={(e) => setTagsEditorInput(e.target.value)}
                    style={{
                      padding: '6px 8px',
                      fontSize: '11.5px',
                      fontFamily: 'var(--font-mono)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-subtle)',
                      color: 'var(--text-primary)'
                    }}
                  />
                  <button
                    type="button"
                    data-testid="okf-save-tags-btn"
                    onClick={handleSaveArtifactTags}
                    disabled={!selectedTagPath.trim() || !targetProjectUuid}
                    style={{
                      padding: '6px 10px',
                      fontSize: '11.5px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: 'var(--accent-indigo)',
                      color: '#ffffff',
                      cursor: 'pointer'
                    }}
                  >
                    Guardar Tags
                  </button>
                </div>

                {labFreshness?.summary && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', paddingTop: '4px' }}>
                    <span className="mcp-badge-fresh">FRESH {labFreshness.summary.fresh ?? 0}</span>
                    <span className="mcp-badge-aging">AGING {labFreshness.summary.aging ?? 0}</span>
                    <span className="mcp-badge-stale">STALE {labFreshness.summary.stale ?? 0}</span>
                    <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                      Firmas Ed25519: {labFreshness.summary.verified_signatures ?? 0} · Sin firma: {labFreshness.summary.unverified_adrs ?? 0}
                    </span>
                  </div>
                )}
              </section>

              {/* (c) Puerta con Llave para Conectores Externos */}
              <section
                data-testid="extension-gates-section"
                style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px'
                }}
              >
                <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Key size={14} color="var(--accent-warning)" /> Puerta con Llave — Conectores Externos (Ed25519)
                </h5>

                {gateFeedback && (
                  <div style={{
                    padding: '6px 10px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '11px',
                    backgroundColor: gateFeedback.type === 'error' ? 'rgba(239, 68, 68, 0.14)' : 'rgba(var(--accent-primary-rgb), 0.14)',
                    color: gateFeedback.type === 'error' ? 'var(--accent-danger)' : 'var(--accent-success)'
                  }}>
                    {gateFeedback.text}
                  </div>
                )}

                {(extensionGates.length > 0
                  ? extensionGates
                  : [
                      { connector_id: 'outbound_rest', unlocked: false, allowed_domains: [], allow_private_ips: false, circuit_state: 'closed' },
                      { connector_id: 'agent_terminal_matrix', unlocked: false, allowed_domains: [], allow_private_ips: false, circuit_state: 'closed' }
                    ]
                ).map(gate => {
                  const id = gate.connector_id;
                  const domainsVal = gateDomainInputs[id] ?? (Array.isArray(gate.allowed_domains) ? gate.allowed_domains.join(', ') : '');
                  const privateIpsVal = gatePrivateIpsInputs[id] ?? Boolean(gate.allow_private_ips);

                  return (
                    <div
                      key={id}
                      data-testid={`gate-card-${id}`}
                      style={{
                        backgroundColor: 'var(--bg-subtle)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-sm)',
                        padding: '10px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '8px'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '6px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          {gate.unlocked ? <Unlock size={13} color="var(--accent-success)" /> : <Lock size={13} color="var(--accent-warning)" />}
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 700 }}>{id}</span>
                          <span className={gate.unlocked ? 'mcp-badge-fresh' : 'mcp-badge-aging'}>
                            {gate.unlocked ? 'DESBLOQUEADA' : 'CERRADA'}
                          </span>
                          {gate.circuit_state && (
                            <span style={{ fontSize: '10.5px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                              circuit: {gate.circuit_state}
                            </span>
                          )}
                        </div>

                        <div style={{ display: 'flex', gap: '6px' }}>
                          <button
                            type="button"
                            data-testid={`unlock-gate-${id}`}
                            onClick={() => handleUnlockGate(id)}
                            style={{
                              padding: '4px 9px',
                              fontSize: '11px',
                              fontWeight: 600,
                              borderRadius: 'var(--radius-sm)',
                              border: 'none',
                              backgroundColor: 'var(--accent-primary)',
                              color: '#ffffff',
                              cursor: 'pointer'
                            }}
                          >
                            Desbloquear con Llave Ed25519
                          </button>
                          <button
                            type="button"
                            data-testid={`lock-gate-${id}`}
                            onClick={() => handleLockGate(id)}
                            style={{
                              padding: '4px 9px',
                              fontSize: '11px',
                              fontWeight: 600,
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-subtle)',
                              backgroundColor: 'var(--bg-surface)',
                              color: 'var(--text-primary)',
                              cursor: 'pointer'
                            }}
                          >
                            Cerrar Puerta
                          </button>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                        <input
                          type="text"
                          data-testid={`gate-domains-${id}`}
                          placeholder="Dominios permitidos (ej: api.github.com, docs.python.org)"
                          value={domainsVal}
                          onChange={(e) => setGateDomainInputs(prev => ({ ...prev, [id]: e.target.value }))}
                          style={{
                            flex: 1,
                            minWidth: '200px',
                            padding: '5px 8px',
                            fontSize: '11px',
                            fontFamily: 'var(--font-mono)',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-subtle)',
                            backgroundColor: 'var(--bg-surface)',
                            color: 'var(--text-primary)'
                          }}
                        />
                        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '11px', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={privateIpsVal}
                            onChange={(e) => setGatePrivateIpsInputs(prev => ({ ...prev, [id]: e.target.checked }))}
                          />
                          Permitir IPs privadas
                        </label>
                      </div>
                    </div>
                  );
                })}

                {/* Probador de URL en conector outbound_rest */}
                <div style={{ display: 'flex', gap: '6px', alignItems: 'center', borderTop: '1px solid var(--border-subtle)', paddingTop: '8px' }}>
                  <Globe size={13} color="var(--text-muted)" />
                  <input
                    type="text"
                    data-testid="rest-gate-test-url"
                    placeholder="https://api.github.com/zen"
                    value={restTestUrl}
                    onChange={(e) => setRestTestUrl(e.target.value)}
                    style={{
                      flex: 1,
                      padding: '6px 8px',
                      fontSize: '11.5px',
                      fontFamily: 'var(--font-mono)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-subtle)',
                      color: 'var(--text-primary)'
                    }}
                  />
                  <button
                    type="button"
                    data-testid="rest-gate-test-btn"
                    onClick={handleTestRestGate}
                    style={{
                      padding: '6px 10px',
                      fontSize: '11.5px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-surface)',
                      color: 'var(--text-primary)',
                      cursor: 'pointer'
                    }}
                  >
                    Probar URL REST
                  </button>
                </div>
                {restTestResult && (
                  <div
                    data-testid="rest-gate-test-result"
                    style={{
                      padding: '7px 10px',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '11px',
                      fontFamily: 'var(--font-mono)',
                      backgroundColor: restTestResult.success ? 'rgba(var(--accent-primary-rgb), 0.14)' : 'rgba(239, 68, 68, 0.14)',
                      color: restTestResult.success ? 'var(--accent-success)' : 'var(--accent-danger)'
                    }}
                  >
                    {restTestResult.success
                      ? `OK (${restTestResult.status_code ?? 200}): ${restTestResult.preview || 'Acceso permitido'}`
                      : `Bloqueado: ${restTestResult.blocked_reason || 'Sin acceso'}`}
                  </div>
                )}
              </section>

              {/* (d) Banco de Autodiagnóstico en Vivo */}
              <section
                data-testid="system-diagnostics-section"
                style={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                  <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <ShieldCheck size={14} color="var(--accent-success)" /> Banco de Autodiagnóstico en Vivo
                  </h5>
                  <button
                    type="button"
                    data-testid="run-diagnostics-btn"
                    onClick={handleRunDiagnostics}
                    disabled={diagnosticsLoading}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      padding: '6px 12px',
                      fontSize: '12px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: 'var(--accent-primary)',
                      color: '#ffffff',
                      cursor: diagnosticsLoading ? 'not-allowed' : 'pointer',
                      opacity: diagnosticsLoading ? 0.65 : 1
                    }}
                  >
                    <Activity size={13} /> {diagnosticsLoading ? 'Ejecutando...' : 'Ejecutar Diagnóstico Completo'}
                  </button>
                </div>

                {diagnosticsResult && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {diagnosticsResult.summary && (
                      <div style={{ fontSize: '11.5px', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                        Resumen: <span style={{ color: 'var(--accent-success)' }}>{diagnosticsResult.summary.passed ?? 0} OK</span>
                        {' · '}
                        <span style={{ color: (diagnosticsResult.summary.failed ?? 0) > 0 ? 'var(--accent-danger)' : 'var(--text-muted)' }}>
                          {diagnosticsResult.summary.failed ?? 0} Fallos
                        </span>
                      </div>
                    )}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                      {(diagnosticsResult.checks || []).map((chk, idx) => {
                        const ok = chk.status === 'passed' || chk.status === 'ok' || chk.status === 'pass';
                        return (
                          <div
                            key={`${chk.name}-${idx}`}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                              gap: '8px',
                              padding: '6px 9px',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: 'var(--bg-subtle)',
                              fontSize: '11px'
                            }}
                          >
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                              <span style={{ fontWeight: 600 }}>{chk.name}</span>
                              {chk.detail && (
                                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10.5px', color: 'var(--text-secondary)' }}>
                                  {chk.detail}
                                </span>
                              )}
                            </div>
                            <span className={ok ? 'mcp-badge-fresh' : 'mcp-badge-stale'}>
                              {String(chk.status || '').toUpperCase()}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </section>
            </div>
          )}

        </div>
      </aside>
    </>
  );
}
