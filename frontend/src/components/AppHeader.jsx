import React, { useState, useEffect, useRef } from 'react';
import {
  Sun,
  Moon,
  Settings,
  ArrowLeft,
  GitBranch,
  Users,
  Github,
  ShieldCheck,
  BookOpen,
  LayoutGrid,
  SlidersHorizontal,
  ArrowUp,
  ArrowDown,
  Eye,
  EyeOff,
  HelpCircle
} from 'lucide-react';
import BrandMark from './BrandMark';

export const NAVBAR_CONFIG_STORAGE_KEY = 'mcp2p_navbar_tabs_v2';
export const NAVBAR_CONFIG_EVENT = 'mcp2p-navbar-config-updated';

export const DEFAULT_WORKSPACE_TABS = [
  { id: 'kanban', label: 'Tablero Kanban', title: 'Tablero Kanban & Leases', icon: LayoutGrid },
  { id: 'changes', label: 'Control de Cambios', title: 'Control de Cambios & Grafo Git', icon: GitBranch },
  { id: 'approvals', label: 'Aprobaciones HITL', title: 'HITL — Human in the Loop', icon: ShieldCheck },
  { id: 'okf', label: 'Gobernanza OKF', title: 'Gobernanza OKF (ADRs, Freshness y Firmas)', icon: BookOpen },
  { id: 'collaborators', label: 'Colaboradores P2P', title: 'Colaboradores & Agentes MCP', icon: Users }
];

const VALID_TAB_IDS = DEFAULT_WORKSPACE_TABS.map(t => t.id);

export function getNavbarConfig() {
  const fallback = { order: [...VALID_TAB_IDS], hidden: [] };
  try {
    const raw = window.localStorage.getItem(NAVBAR_CONFIG_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    const rawOrder = Array.isArray(parsed?.order) ? parsed.order : [];
    const seen = new Set();
    const order = [];
    rawOrder.forEach(id => {
      if (VALID_TAB_IDS.includes(id) && !seen.has(id)) {
        seen.add(id);
        order.push(id);
      }
    });
    VALID_TAB_IDS.forEach(id => {
      if (!seen.has(id)) {
        seen.add(id);
        order.push(id);
      }
    });
    const rawHidden = Array.isArray(parsed?.hidden) ? parsed.hidden : [];
    const hidden = rawHidden.filter((id, i, arr) => VALID_TAB_IDS.includes(id) && arr.indexOf(id) === i);
    // Garantizar al menos 1 pestaña visible en la barra
    if (hidden.length >= order.length) {
      return { order, hidden: hidden.slice(0, order.length - 1) };
    }
    return { order, hidden };
  } catch {
    return fallback;
  }
}

export function saveNavbarConfig(config) {
  try {
    window.localStorage.setItem(NAVBAR_CONFIG_STORAGE_KEY, JSON.stringify(config));
    window.dispatchEvent(new CustomEvent(NAVBAR_CONFIG_EVENT, { detail: config }));
  } catch {
    // Ignorar errores de almacenamiento en entornos restringidos
  }
}

export function AppHeader({
  theme,
  onToggleTheme,
  onOpenSettings,
  activeProject,
  onBackToProjects,
  activeTab,
  onTabChange,
  githubStatus,
  projectsCount = 0,
  pendingApprovalsCount = 0
}) {
  const [navConfig, setNavConfig] = useState(() => getNavbarConfig());
  const [isCustomizerOpen, setIsCustomizerOpen] = useState(false);
  const customizerRef = useRef(null);

  useEffect(() => {
    const syncConfig = () => setNavConfig(getNavbarConfig());
    window.addEventListener(NAVBAR_CONFIG_EVENT, syncConfig);
    window.addEventListener('storage', syncConfig);
    return () => {
      window.removeEventListener(NAVBAR_CONFIG_EVENT, syncConfig);
      window.removeEventListener('storage', syncConfig);
    };
  }, []);

  useEffect(() => {
    if (!isCustomizerOpen) return undefined;
    const handleClickOutside = (e) => {
      if (customizerRef.current && !customizerRef.current.contains(e.target)) {
        setIsCustomizerOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isCustomizerOpen]);

  const orderedTabs = navConfig.order
    .map(id => DEFAULT_WORKSPACE_TABS.find(t => t.id === id))
    .filter(Boolean);

  const visibleTabs = orderedTabs.filter(t => !navConfig.hidden.includes(t.id));

  const toggleTabVisibility = (tabId) => {
    const isHidden = navConfig.hidden.includes(tabId);
    if (!isHidden && visibleTabs.length <= 1) return; // Mantener al menos 1 pestaña visible
    const nextHidden = isHidden
      ? navConfig.hidden.filter(id => id !== tabId)
      : [...navConfig.hidden, tabId];
    const next = { ...navConfig, hidden: nextHidden };
    setNavConfig(next);
    saveNavbarConfig(next);
  };

  const moveTab = (tabId, direction) => {
    const idx = navConfig.order.indexOf(tabId);
    const targetIdx = idx + direction;
    if (idx < 0 || targetIdx < 0 || targetIdx >= navConfig.order.length) return;
    const nextOrder = [...navConfig.order];
    [nextOrder[idx], nextOrder[targetIdx]] = [nextOrder[targetIdx], nextOrder[idx]];
    const next = { ...navConfig, order: nextOrder };
    setNavConfig(next);
    saveNavbarConfig(next);
  };

  const isGithubConnected = Boolean(githubStatus?.authenticated);

  return (
    <header className="mcp-header">
      {/* 1. SECCIÓN IZQUIERDA: BrandMark + Flecha Minimalista Volver al Hub */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, height: '100%' }}>
        <BrandMark size={24} />

        <span style={{ color: 'var(--border-subtle)', fontWeight: 300, display: 'inline-flex', alignItems: 'center' }}>|</span>

        {activeProject ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
            <button
              type="button"
              data-testid="back-to-hub-btn"
              aria-label="Volver al Hub de Proyectos"
              title="Volver al Hub de Proyectos"
              onClick={() => onBackToProjects && onBackToProjects()}
              style={{
                width: '30px',
                height: '30px',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-subtle)',
                backgroundColor: 'var(--bg-subtle)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                transition: 'var(--transition)',
                flexShrink: 0
              }}
            >
              <ArrowLeft size={15} />
            </button>
            <span
              data-testid="active-project-name"
              title={`Proyecto activo: ${activeProject.name}`}
              style={{
                fontSize: '13px',
                fontWeight: 600,
                color: 'var(--text-primary)',
                maxWidth: 'clamp(100px, 16vw, 220px)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap'
              }}
            >
              {activeProject.name}
            </span>
          </div>
        ) : (
          <span
            data-testid="hub-header-label"
            style={{
              fontSize: '12.5px',
              fontWeight: 600,
              color: 'var(--text-secondary)'
            }}
          >
            Hub de Proyectos ({projectsCount})
          </span>
        )}
      </div>

      {/* 2. SECCIÓN CENTRAL: Iconos Cuadrados de Navegación + Hover/Activo en Grande + Personalización */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, minWidth: 0, height: '100%' }}>
        {activeProject ? (
          <div ref={customizerRef} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            <nav
              aria-label="Navegación del proyecto"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                height: '36px'
              }}
            >
              {visibleTabs.map(tab => {
                const Icon = tab.icon;
                const isSelected = activeTab === tab.id || (tab.id === 'changes' && activeTab === 'branches');
                return (
                  <button
                    key={tab.id}
                    type="button"
                    data-testid={`tab-${tab.id}`}
                    className={`mcp-nav-icon-tab ${isSelected ? 'is-active' : ''}`}
                    aria-pressed={isSelected}
                    aria-label={tab.label}
                    title={tab.title || tab.label}
                    onClick={() => onTabChange && onTabChange(tab.id)}
                  >
                    <Icon size={15} style={{ flexShrink: 0 }} />
                    {tab.id === 'approvals' && pendingApprovalsCount > 0 && (
                      <span
                        style={{
                          position: 'absolute',
                          top: '2px',
                          right: '2px',
                          width: '7px',
                          height: '7px',
                          borderRadius: '50%',
                          backgroundColor: 'var(--accent-primary)'
                        }}
                      />
                    )}
                    <span className="mcp-nav-tab-label">{tab.label}</span>
                  </button>
                );
              })}

              {/* Botón para agregar/quitar o reordenar secciones de la barra */}
              <button
                type="button"
                data-testid="customize-navbar-btn"
                aria-label="Personalizar pestañas de la barra"
                title="Agregar, quitar o reordenar pestañas visibles"
                onClick={() => setIsCustomizerOpen(prev => !prev)}
                style={{
                  width: '32px',
                  height: '32px',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 'var(--radius-md)',
                  border: isCustomizerOpen
                    ? '1px solid var(--accent-primary)'
                    : '1px dashed var(--border-subtle)',
                  backgroundColor: isCustomizerOpen ? 'rgba(var(--accent-primary-rgb), 0.12)' : 'transparent',
                  color: isCustomizerOpen ? 'var(--accent-primary)' : 'var(--text-muted)',
                  cursor: 'pointer',
                  transition: 'var(--transition)',
                  flexShrink: 0
                }}
              >
                <SlidersHorizontal size={14} />
              </button>
            </nav>

            {isCustomizerOpen && (
              <div
                data-testid="navbar-customizer-popover"
                style={{
                  position: 'absolute',
                  top: '42px',
                  right: 0,
                  width: '285px',
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-lg)',
                  boxShadow: 'var(--shadow-lg)',
                  padding: '12px',
                  zIndex: 80,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '8px'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '6px' }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                    Personalizar Barra Superior
                  </span>
                  <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>
                    {visibleTabs.length}/{orderedTabs.length} activas
                  </span>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                  {orderedTabs.map((tab, idx) => {
                    const Icon = tab.icon;
                    const isHidden = navConfig.hidden.includes(tab.id);
                    const isLastVisible = !isHidden && visibleTabs.length <= 1;
                    return (
                      <div
                        key={tab.id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: '6px',
                          padding: '5px 7px',
                          borderRadius: 'var(--radius-sm)',
                          backgroundColor: activeTab === tab.id ? 'rgba(var(--accent-primary-rgb), 0.08)' : 'var(--bg-subtle)',
                          border: '1px solid var(--border-subtle)'
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            onTabChange && onTabChange(tab.id);
                            setIsCustomizerOpen(false);
                          }}
                          title={`Ir a ${tab.label}`}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '7px',
                            border: 'none',
                            background: 'transparent',
                            color: isHidden ? 'var(--text-muted)' : 'var(--text-primary)',
                            fontSize: '12px',
                            fontWeight: 600,
                            cursor: 'pointer',
                            flex: 1,
                            textAlign: 'left',
                            minWidth: 0
                          }}
                        >
                          <Icon size={13} color={isHidden ? 'var(--text-muted)' : 'var(--accent-primary)'} />
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {tab.label}
                          </span>
                        </button>

                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '3px', flexShrink: 0 }}>
                          <button
                            type="button"
                            data-testid={`navbar-move-up-${tab.id}`}
                            disabled={idx === 0}
                            onClick={() => moveTab(tab.id, -1)}
                            title="Subir orden"
                            style={{
                              width: '22px',
                              height: '22px',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              border: '1px solid var(--border-subtle)',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: 'var(--bg-surface)',
                              color: idx === 0 ? 'var(--text-muted)' : 'var(--text-secondary)',
                              cursor: idx === 0 ? 'not-allowed' : 'pointer',
                              opacity: idx === 0 ? 0.4 : 1
                            }}
                          >
                            <ArrowUp size={11} />
                          </button>
                          <button
                            type="button"
                            data-testid={`navbar-move-down-${tab.id}`}
                            disabled={idx === orderedTabs.length - 1}
                            onClick={() => moveTab(tab.id, 1)}
                            title="Bajar orden"
                            style={{
                              width: '22px',
                              height: '22px',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              border: '1px solid var(--border-subtle)',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: 'var(--bg-surface)',
                              color: idx === orderedTabs.length - 1 ? 'var(--text-muted)' : 'var(--text-secondary)',
                              cursor: idx === orderedTabs.length - 1 ? 'not-allowed' : 'pointer',
                              opacity: idx === orderedTabs.length - 1 ? 0.4 : 1
                            }}
                          >
                            <ArrowDown size={11} />
                          </button>
                          <button
                            type="button"
                            data-testid={`navbar-toggle-${tab.id}`}
                            disabled={isLastVisible}
                            onClick={() => toggleTabVisibility(tab.id)}
                            title={isHidden ? 'Mostrar en barra superior' : isLastVisible ? 'Debe haber al menos 1 pestaña' : 'Ocultar de barra superior'}
                            style={{
                              width: '24px',
                              height: '22px',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              border: '1px solid var(--border-subtle)',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: isHidden ? 'transparent' : 'rgba(var(--accent-primary-rgb), 0.14)',
                              color: isHidden ? 'var(--text-muted)' : 'var(--accent-primary)',
                              cursor: isLastVisible ? 'not-allowed' : 'pointer'
                            }}
                          >
                            {isHidden ? <EyeOff size={11} /> : <Eye size={11} />}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <button
                  type="button"
                  onClick={() => {
                    setIsCustomizerOpen(false);
                    onOpenSettings && onOpenSettings('manual');
                  }}
                  style={{
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '6px 8px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    backgroundColor: 'var(--bg-subtle)',
                    color: 'var(--text-secondary)',
                    cursor: 'pointer',
                    textAlign: 'center'
                  }}
                >
                  Gestionar y ver guía en Configuración → About
                </button>
              </div>
            )}
          </div>
        ) : null}
      </div>

      {/* 3. SECCIÓN DERECHA: Solo Iconos Compactos (GitHub Verde/Rojo, Tema, About, Configuración) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', justifyContent: 'flex-end', height: '100%', flexShrink: 0 }}>
        {/* Icono GitHub con indicador verde (conectado) o rojo (desconectado), sin usuario en texto */}
        <button
          type="button"
          data-testid="github-header-btn"
          onClick={() => onOpenSettings('github')}
          aria-label="Estado de sesión GitHub Auth"
          title={isGithubConnected
            ? 'GitHub conectado — Ver sesión activa e identidad en Configuración → GitHub Auth'
            : 'GitHub no conectado — Configurar sesión en Configuración → GitHub Auth'
          }
          style={{
            position: 'relative',
            width: '32px',
            height: '32px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-md)',
            border: `1px solid ${isGithubConnected ? 'rgba(var(--accent-primary-rgb), 0.45)' : 'rgba(239, 68, 68, 0.45)'}`,
            backgroundColor: isGithubConnected ? 'rgba(var(--accent-primary-rgb), 0.1)' : 'rgba(239, 68, 68, 0.08)',
            color: isGithubConnected ? 'var(--accent-success)' : 'var(--accent-danger)',
            cursor: 'pointer',
            transition: 'var(--transition)'
          }}
        >
          <Github size={16} />
          <span
            data-testid="github-status-dot"
            style={{
              position: 'absolute',
              top: '4px',
              right: '4px',
              width: '7px',
              height: '7px',
              borderRadius: '50%',
              backgroundColor: isGithubConnected ? 'var(--accent-success)' : 'var(--accent-danger)',
              boxShadow: '0 0 0 1.5px var(--bg-surface)'
            }}
          />
        </button>

        {/* Alternar Tema */}
        <button
          type="button"
          onClick={onToggleTheme}
          title="Alternar Tema Claro / Oscuro (Ctrl+D)"
          aria-label="Alternar tema"
          style={{
            width: '32px',
            height: '32px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            transition: 'var(--transition)'
          }}
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>

        {/* Acceso Rápido a About / Guía de Iconos */}
        <button
          type="button"
          data-testid="about-header-btn"
          onClick={() => onOpenSettings('manual')}
          title="About, Atajos y Referencia de Módulos (F1)"
          aria-label="About y Manual"
          style={{
            width: '32px',
            height: '32px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            transition: 'var(--transition)'
          }}
        >
          <HelpCircle size={15} />
        </button>

        {/* Botón de Configuración */}
        <button
          type="button"
          onClick={() => onOpenSettings()}
          title="Configuración del Nodo, P2P y Agentes (F1)"
          aria-label="Configuración"
          style={{
            width: '32px',
            height: '32px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            transition: 'var(--transition)'
          }}
        >
          <Settings size={16} />
        </button>
      </div>
    </header>
  );
}
