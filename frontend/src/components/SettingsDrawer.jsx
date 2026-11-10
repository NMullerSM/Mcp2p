import React, { useState, useEffect } from 'react';
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
  File,
  UserCheck
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

  // Estados del Visor OKF en Crudo
  const [okfTree, setOkfTree] = useState([]);
  const [selectedFilePath, setSelectedFilePath] = useState('');
  const [selectedFileContent, setSelectedFileContent] = useState('');
  const [fileLoading, setFileLoading] = useState(false);
  const [viewMode, setViewMode] = useState('raw'); // 'raw' | 'preview'

  const targetProject = activeProject || (projectsList && projectsList[0]);
  const targetProjectUuid = targetProject?.project_uuid;

  // Sincronizar tab si cambia initialTab al abrir
  useEffect(() => {
    if (isOpen && initialTab) {
      setActiveTab(initialTab);
    }
  }, [isOpen, initialTab]);

  const loadOkfTree = async (isCancelled = () => false) => {
    if (!api || !targetProject) return;
    try {
      const res = await api.get_okf_raw_tree(targetProject.project_uuid);
      if (isCancelled()) return;
      if (res.success && res.tree?.length > 0) {
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
      const res = await api.read_okf_file(targetProject.project_uuid, path);
      if (res.success) {
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

    // Cargar datos del sistema
    api.get_system_info().then(info => {
      if (cancelled) return;
      if (info && info.success) setSystemInfo(info);
    });

    // Cargar estado de GitHub
    api.get_github_status().then(res => {
      if (cancelled) return;
      if (res && res.success) {
        setGithubStatus(res.github);
        if (onGithubStatusChange) onGithubStatusChange(res.github);
      }
    });

    // Cargar enlace P2P
    if (targetProjectUuid) {
      api.generate_p2p_invite(targetProjectUuid).then(res => {
        if (cancelled) return;
        if (res && res.success) setInviteUrl(res.invite_url);
      });

      api.get_mcp_config_snippet(targetProjectUuid).then(res => {
        if (cancelled) return;
        if (res && res.success) {
          setMcpConfig(res.mcp_json);
          setAgentPrompt(res.agent_seed_prompt);
        }
      });

      loadOkfTree(() => cancelled);
    }

    return () => { cancelled = true; };
  }, [isOpen, api, targetProjectUuid, onGithubStatusChange]);

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

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.45)',
            backdropFilter: 'blur(3px)',
            zIndex: 90
          }}
        />
      )}

      {/* Drawer Container (Totalmente Responsive) */}
      <aside
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
                  backgroundColor: 'rgba(99, 102, 241, 0.08)',
                  border: '1px solid rgba(99, 102, 241, 0.25)',
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
                    backgroundColor: githubStatus?.authenticated ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
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
                  backgroundColor: patMessage.type === 'success' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                  color: patMessage.type === 'success' ? 'var(--accent-success)' : 'var(--accent-danger)',
                  border: `1px solid ${patMessage.type === 'success' ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`
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
                    6. Visor OKF & Archivos en Crudo
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    Ubicado en la primera pestaña de este drawer lateral. Te permite explorar el árbol de archivos de gobernanza (<code>docs/okf/</code>), leer especificaciones de arquitectura y revisar los registros de decisión de arquitectura (ADRs) sin recargar la interfaz principal.
                  </div>
                </div>
              </div>
            </div>
          )}

        </div>
      </aside>
    </>
  );
}
