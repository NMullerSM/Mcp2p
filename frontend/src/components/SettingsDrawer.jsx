import React, { useState, useEffect } from 'react';
import { X, Copy, Check, Terminal, Share2, Github, BookOpen, ShieldCheck } from 'lucide-react';

export function SettingsDrawer({ isOpen, onClose, api, activeProject, projectsList }) {
  const [activeTab, setActiveTab] = useState('p2p');
  const [inviteUrl, setInviteUrl] = useState('');
  const [mcpConfig, setMcpConfig] = useState('');
  const [agentPrompt, setAgentPrompt] = useState('');
  const [githubStatus, setGithubStatus] = useState(null);
  const [systemInfo, setSystemInfo] = useState(null);
  const [copiedLink, setCopiedLink] = useState(false);
  const [copiedConfig, setCopiedConfig] = useState(false);
  const [copiedPrompt, setCopiedPrompt] = useState(false);

  const targetProject = activeProject || projectsList[0];

  useEffect(() => {
    if (!isOpen || !api) return;

    // Cargar datos del sistema
    api.get_system_info().then(info => {
      if (info.success) setSystemInfo(info);
    });

    // Cargar estado de GitHub
    api.get_github_status().then(res => {
      if (res.success) setGithubStatus(res.github);
    });

    // Cargar enlace P2P
    if (targetProject) {
      api.generate_p2p_invite(targetProject.project_uuid).then(res => {
        if (res.success) setInviteUrl(res.invite_url);
      });

      api.get_mcp_config_snippet(targetProject.project_uuid).then(res => {
        if (res.success) {
          setMcpConfig(res.mcp_json);
          setAgentPrompt(res.agent_seed_prompt);
        }
      });
    }
  }, [isOpen, api, targetProject]);

  const copyToClipboard = (text, setter) => {
    navigator.clipboard.writeText(text);
    setter(true);
    setTimeout(() => setter(false), 2000);
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
            backgroundColor: 'rgba(0, 0, 0, 0.4)',
            backdropFilter: 'blur(2px)',
            zIndex: 90
          }}
        />
      )}

      {/* Drawer Container */}
      <aside
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          width: '500px',
          maxWidth: '92vw',
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
          justifyContent: 'space-between'
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
          borderBottom: '1px solid var(--border-subtle)',
          backgroundColor: 'var(--bg-subtle)',
          padding: '4px 8px 0 8px',
          gap: '4px'
        }}>
          {[
            { id: 'p2p', label: 'Enlace P2P', icon: Share2 },
            { id: 'mcp', label: 'Agente MCP', icon: Terminal },
            { id: 'github', label: 'GitHub', icon: Github },
            { id: 'manual', label: 'Manual (F1)', icon: BookOpen },
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
                  background: isSelected ? 'var(--bg-surface)' : 'transparent',
                  color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  borderTopLeftRadius: 'var(--radius-sm)',
                  borderTopRightRadius: 'var(--radius-sm)'
                }}
              >
                <Icon size={14} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Content Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
          
          {/* TAB 1: Enlace P2P */}
          {activeTab === 'p2p' && (
            <div>
              <h4 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>Enlace de Invitación P2P</h4>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                Comparte este enlace firmado criptográficamente para que otro colaborador se conecte y replique las carpetas de este proyecto en su máquina.
              </p>

              <div style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '12px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-muted)' }}>
                  <span>Proyecto: {targetProject ? targetProject.name : 'Ninguno'}</span>
                  <span>Criptografía: Ed25519</span>
                </div>
                <input
                  type="text"
                  readOnly
                  value={inviteUrl}
                  style={{
                    backgroundColor: 'var(--bg-subtle)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '8px 10px',
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    color: 'var(--text-primary)',
                    outline: 'none'
                  }}
                />
                <button
                  onClick={() => copyToClipboard(inviteUrl, setCopiedLink)}
                  style={{
                    alignSelf: 'flex-end',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 12px',
                    fontSize: '12px',
                    fontWeight: 600,
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: copiedLink ? 'var(--accent-success)' : 'var(--accent-primary)',
                    color: '#ffffff',
                    cursor: 'pointer'
                  }}
                >
                  {copiedLink ? <Check size={14} /> : <Copy size={14} />}
                  {copiedLink ? '¡Enlace Copiado!' : 'Copiar Enlace'}
                </button>
              </div>

              <div style={{ marginTop: '16px', background: 'var(--bg-subtle)', padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px', fontSize: '12px', fontWeight: 600 }}>
                  <ShieldCheck size={15} color="var(--accent-success)" />
                  Control de Acceso Human-in-the-Loop
                </div>
                <p style={{ fontSize: '11px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                  Cuando el usuario pegue este enlace, aparecerá en la pestaña de <strong>Colaboradores</strong> de tu proyecto. El agente de IA no puede aprobarlo automáticamente; tú tendrás el botón explícito de <strong>Aceptar</strong>.
                </p>
              </div>
            </div>
          )}

          {/* TAB 2: Agente MCP */}
          {activeTab === 'mcp' && (
            <div>
              <h4 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>Conexión con Agentes (Model Context Protocol)</h4>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
                Conecta tu IDE favorito (Cursor, Claude Desktop, Antigravity IDE) al servidor MCP local para permitir a los agentes colaborar a 0 tokens.
              </p>

              {/* Snippet mcp.json */}
              <div style={{ marginBottom: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <span style={{ fontSize: '12px', fontWeight: 600 }}>1. Configuración JSON para mcp.json</span>
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

          {/* TAB 3: GitHub */}
          {activeTab === 'github' && (
            <div>
              <h4 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>Autenticación & Repositorios GitHub</h4>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
                Estado de la sesión de GitHub para sincronización y publicación de repositorios.
              </p>

              <div style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Estado:</span>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '3px 8px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: githubStatus?.authenticated ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                    color: githubStatus?.authenticated ? 'var(--accent-success)' : 'var(--accent-danger)'
                  }}>
                    {githubStatus?.authenticated ? 'Sesión Activa' : 'No Conectado'}
                  </span>
                </div>

                {githubStatus?.username && (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Usuario:</span>
                    <span style={{ fontSize: '12px', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
                      @{githubStatus.username}
                    </span>
                  </div>
                )}

                <div style={{ fontSize: '11px', color: 'var(--text-muted)', borderTop: '1px solid var(--border-subtle)', paddingTop: '8px' }}>
                  {githubStatus?.details}
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: Manual y Atajos */}
          {activeTab === 'manual' && (
            <div>
              <h4 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>Atajos de Teclado Globales</h4>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                Diseñado bajo las Heurísticas de Nielsen y Ley de Hick para máxima velocidad operativa.
              </p>

              <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
                <tbody>
                  {[
                    ['Ctrl + P', 'Crear Nuevo Proyecto en Hub'],
                    ['Ctrl + N', 'Nueva Tarea Kanban (dentro del proyecto)'],
                    ['Ctrl + D', 'Alternar Tema Claro / Oscuro'],
                    ['F1', 'Abrir / Cerrar esta Guía de Configuración'],
                    ['ESC', 'Cerrar cualquier ventana modal o drawer']
                  ].map(([key, desc]) => (
                    <tr key={key} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '8px 0', fontFamily: 'var(--font-mono)' }}>
                        <span style={{
                          backgroundColor: 'var(--bg-subtle)',
                          border: '1px solid var(--border-subtle)',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          fontSize: '11px'
                        }}>
                          {key}
                        </span>
                      </td>
                      <td style={{ padding: '8px 0', color: 'var(--text-secondary)' }}>{desc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

        </div>
      </aside>
    </>
  );
}
