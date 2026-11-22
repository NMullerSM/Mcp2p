import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { MessageSquare, X } from 'lucide-react';
import { ChatFeed } from './ChatFeed';
import { ChatComposer } from './ChatComposer';
import { useChatFeed } from './useChatFeed';
import { useChatChannels } from './useChatChannels';
import { countUnread } from './chatModel';

const CONVERSATION_TAB = 'conversacion';
const OPEN_KEY = 'mcp2p.chat.open';
// Mismo punto de corte que el resto de la interfaz: por debajo, el chat ocupa toda la pantalla.
const WIDE_MIN_PX = 768;
const PANEL_WIDTH_PX = 380;
// Alturas de la barra superior (.mcp-header, 50 px) y del pie (28 px). El panel de escritorio
// va entre ambas para no tapar la barra; las constantes no se importan para mantener el modulo aislado.
const HEADER_HEIGHT_PX = 50;
const FOOTER_HEIGHT_PX = 28;

function readOpen() {
  try {
    return window.localStorage.getItem(OPEN_KEY) === '1';
  } catch {
    return false;
  }
}

function writeOpen(value) {
  try {
    window.localStorage.setItem(OPEN_KEY, value ? '1' : '0');
  } catch {
    // Sin persistencia el chat se abre cerrado la siguiente vez; no es un error.
  }
}

function useIsWide() {
  const [wide, setWide] = useState(() => window.innerWidth >= WIDE_MIN_PX);
  useEffect(() => {
    const onResize = () => setWide(window.innerWidth >= WIDE_MIN_PX);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return wide;
}

// Los ids de pestaña salen en atributos del DOM: se limpian para no depender de lo que mande la app.
function domSafe(value) {
  return String(value).replace(/[^\w-]/g, '_');
}

function CountBadge({ value }) {
  if (!value) return null;
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: '18px',
        height: '18px',
        padding: '0 5px',
        marginLeft: '6px',
        borderRadius: 'var(--radius-full)',
        backgroundColor: 'var(--accent-danger)',
        color: '#ffffff',
        fontSize: '10px',
        fontWeight: 700
      }}
    >
      {value > 99 ? '99+' : value}
    </span>
  );
}

/**
 * Chat global: boton flotante con insignia y panel con Conversacion y las pestañas que la app
 * componga en `extraTabs` (el modulo no las conoce). No pinta nada si el puente no tiene el feed
 * o no hay proyecto activo.
 */
export function ChatDock({ api, projectUuid, extraTabs = [] }) {
  const canChat = typeof api?.chat_get_feed === 'function' && Boolean(projectUuid);
  const feedApi = canChat ? api : null;
  const feedUuid = canChat ? projectUuid : null;

  const [open, setOpenState] = useState(readOpen);
  const [activeTab, setActiveTab] = useState(CONVERSATION_TAB);
  const wide = useIsWide();
  const uid = useId();
  const panelId = `${uid}-panel`;
  const headingId = `${uid}-heading`;
  const tabPanelId = `${uid}-tabpanel`;
  const buttonRef = useRef(null);
  const panelRef = useRef(null);
  const focusPanelOnOpenRef = useRef(false);

  const feed = useChatFeed({ api: feedApi, projectUuid: feedUuid, open });
  const { channels, participants, error: channelsError } = useChatChannels({ api: feedApi, projectUuid: feedUuid });

  const unread = countUnread(feed.entries, feed.lastSeenMs);
  const extras = Array.isArray(extraTabs) ? extraTabs.filter(Boolean) : [];
  const extraBadge = extras.reduce((sum, tab) => sum + (Number(tab.badge) || 0), 0);
  const tabs = [
    { id: CONVERSATION_TAB, label: 'Conversación', badge: unread },
    ...extras.map((tab) => ({ id: tab.id, label: tab.label, badge: Number(tab.badge) || 0 }))
  ];
  const currentTab = tabs.some((tab) => tab.id === activeTab) ? activeTab : CONVERSATION_TAB;

  const setOpen = useCallback((value) => {
    writeOpen(value);
    setOpenState(value);
  }, []);

  const openPanel = () => {
    focusPanelOnOpenRef.current = true;
    setOpen(true);
  };

  // Al cerrar, el foco vuelve al boton flotante: quien abrio el chat no pierde su sitio.
  const closePanel = useCallback(() => {
    setOpen(false);
    buttonRef.current?.focus();
  }, [setOpen]);

  useEffect(() => {
    if (open && focusPanelOnOpenRef.current) {
      focusPanelOnOpenRef.current = false;
      panelRef.current?.focus();
    }
  }, [open]);

  // Con la Conversacion a la vista, lo que llega se da por leido.
  useEffect(() => {
    if (open && currentTab === CONVERSATION_TAB) feed.markSeen();
  }, [open, currentTab, feed.entries, feed.markSeen]);

  if (!canChat) return null;

  const total = unread + extraBadge;
  const extraNote = extraBadge > 0 ? `, ${extraBadge} pendientes en otras pestañas` : '';
  const buttonLabel = `${open ? 'Cerrar' : 'Abrir'} chat: ${unread} sin leer${extraNote}`;

  const floatingStyle = {
    position: 'fixed',
    right: wide && open ? `${PANEL_WIDTH_PX + 16}px` : '16px',
    bottom: `${FOOTER_HEIGHT_PX + 12}px`,
    zIndex: 55,
    width: '44px',
    height: '44px',
    borderRadius: '50%',
    border: '1px solid var(--accent-primary)',
    backgroundColor: 'var(--accent-primary)',
    color: '#ffffff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    boxShadow: 'var(--shadow-md)'
  };

  const panelStyle = wide
    ? {
        position: 'fixed',
        top: `${HEADER_HEIGHT_PX}px`,
        right: 0,
        bottom: `${FOOTER_HEIGHT_PX}px`,
        width: `${PANEL_WIDTH_PX}px`,
        maxWidth: '100vw',
        borderLeft: '1px solid var(--border-subtle)'
      }
    : {
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        left: 0,
        width: '100%'
      };

  const chipStyle = (available) => ({
    display: 'inline-flex',
    alignItems: 'center',
    maxWidth: '100%',
    padding: '2px 8px',
    borderRadius: 'var(--radius-full)',
    border: '1px solid var(--border-subtle)',
    backgroundColor: available ? 'var(--bg-subtle)' : 'transparent',
    color: available ? 'var(--text-primary)' : 'var(--text-muted)',
    fontSize: '11px',
    fontWeight: 600,
    overflowWrap: 'anywhere'
  });

  const tabStyle = (selected) => ({
    display: 'inline-flex',
    alignItems: 'center',
    flexShrink: 0,
    padding: '6px 10px',
    fontSize: '12px',
    fontWeight: 600,
    background: 'transparent',
    border: 'none',
    borderBottom: `2px solid ${selected ? 'var(--accent-primary)' : 'transparent'}`,
    color: selected ? 'var(--text-primary)' : 'var(--text-secondary)',
    cursor: 'pointer'
  });

  const onPanelKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      closePanel();
    }
  };

  const activeExtra = extras.find((tab) => tab.id === currentTab);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => (open ? closePanel() : openPanel())}
        aria-label={buttonLabel}
        aria-expanded={open}
        aria-controls={panelId}
        title="Chat del proyecto"
        style={floatingStyle}
      >
        <MessageSquare size={20} aria-hidden="true" />
        {total > 0 && (
          <span
            data-testid="chat-dock-badge"
            aria-hidden="true"
            style={{
              position: 'absolute',
              top: '-4px',
              right: '-4px',
              minWidth: '18px',
              height: '18px',
              padding: '0 5px',
              borderRadius: 'var(--radius-full)',
              backgroundColor: 'var(--accent-danger)',
              color: '#ffffff',
              fontSize: '10px',
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            {total > 99 ? '99+' : total}
          </span>
        )}
      </button>

      {open && (
        <section
          id={panelId}
          ref={panelRef}
          role="dialog"
          aria-labelledby={headingId}
          tabIndex={-1}
          onKeyDown={onPanelKeyDown}
          data-testid="chat-dock-panel"
          style={{
            ...panelStyle,
            zIndex: 60,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            backgroundColor: 'var(--bg-surface)',
            boxShadow: 'var(--shadow-lg)',
            outline: 'none'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', padding: '10px 12px', borderBottom: '1px solid var(--border-subtle)', flexShrink: 0 }}>
            <h2 id={headingId} style={{ margin: 0, fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)' }}>
              Chat
            </h2>
            <button
              type="button"
              onClick={closePanel}
              aria-label="Cerrar chat"
              title="Cerrar chat"
              style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '28px', height: '28px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-subtle)', color: 'var(--text-primary)', cursor: 'pointer' }}
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>

          {(channels.length > 0 || channelsError) && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '8px 12px', borderBottom: '1px solid var(--border-subtle)', flexShrink: 0, minWidth: 0 }}>
              {channels.length > 0 && (
                <ul aria-label="Canales del chat" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', margin: 0, padding: 0, listStyle: 'none' }}>
                  {channels.map((channel) => {
                    const available = Boolean(channel.available);
                    return (
                      <li key={channel.kind} style={{ maxWidth: '100%' }}>
                        <span
                          data-testid={`chat-channel-${channel.kind}`}
                          aria-disabled={available ? undefined : 'true'}
                          title={available ? channel.label : channel.reason || `${channel.label}: no disponible`}
                          style={chipStyle(available)}
                        >
                          {channel.label}{available ? '' : ' (no disponible)'}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              {channels.filter((c) => !c.available && c.reason).map((c) => (
                <p key={`reason-${c.kind}`} style={{ margin: 0, fontSize: '10.5px', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                  {c.label}: {c.reason}
                </p>
              ))}
              {channelsError && (
                <p style={{ margin: 0, fontSize: '10.5px', color: 'var(--accent-warning)', lineHeight: 1.4 }}>
                  Canales: {channelsError}
                </p>
              )}
            </div>
          )}

          <div role="tablist" aria-label="Secciones del chat" style={{ display: 'flex', gap: '2px', padding: '0 12px', borderBottom: '1px solid var(--border-subtle)', flexShrink: 0, overflowX: 'auto', minWidth: 0 }}>
            {tabs.map((tab) => {
              const selected = tab.id === currentTab;
              const tabDomId = `${uid}-tab-${domSafe(tab.id)}`;
              return (
                <button
                  key={String(tab.id)}
                  id={tabDomId}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls={tabPanelId}
                  onClick={() => setActiveTab(tab.id)}
                  style={tabStyle(selected)}
                >
                  {tab.label}
                  <CountBadge value={tab.badge} />
                </button>
              );
            })}
          </div>

          <div
            role="tabpanel"
            id={tabPanelId}
            aria-labelledby={`${uid}-tab-${domSafe(currentTab)}`}
            style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 }}
          >
            {currentTab === CONVERSATION_TAB ? (
              <>
                <ChatFeed
                  entries={feed.entries}
                  sources={feed.sources}
                  error={feed.error}
                  loading={feed.loading}
                  onRetry={feed.refresh}
                />
                <ChatComposer
                  api={api}
                  projectUuid={projectUuid}
                  participants={participants}
                  onSent={() => feed.refresh()}
                />
              </>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0, overflowY: 'auto' }}>
                {activeExtra && typeof activeExtra.render === 'function' ? activeExtra.render() : null}
              </div>
            )}
          </div>
        </section>
      )}
    </>
  );
}
