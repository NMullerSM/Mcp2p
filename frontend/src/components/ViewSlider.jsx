import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState
} from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, X } from 'lucide-react';

/**
 * Slider lateral derecho de la vista activa (una pestana delgada pegada al borde derecho
 * que despliega un panel con las herramientas SECUNDARIAS de la vista).
 *
 * Arquitectura (contexto + portal, sin cablear estado en ProjectWorkspace):
 *   - <ViewSliderProvider viewKey=...> envuelve el espacio de trabajo y pinta UNA pestana y
 *     UN panel (el "host"). `viewKey` identifica la vista: cuando cambia, el slider vuelve
 *     a estar cerrado (estado abierto/cerrado por vista; el scroll interno nunca se recuerda).
 *   - Cada vista declara su contenido con <ViewSliderContent title="...">hijos</ViewSliderContent>
 *     desde donde viven los estados/handlers que usa. Los hijos se montan con un portal dentro
 *     del cuerpo del panel pero siguen siendo parte del arbol React de la vista, asi que sus
 *     estados y handlers no se pierden al cerrar el panel.
 *   - useViewSlider() devuelve { isOpen, open, close, toggle } por si una vista necesita
 *     abrir o cerrar el panel por codigo (p. ej. cerrar antes de abrir un modal).
 *   - Fuera de un provider (tests unitarios de una vista aislada) ambos son inocuos:
 *     ViewSliderContent no pinta nada y useViewSlider devuelve funciones vacias.
 */

const ViewSliderContext = createContext(null);

const CLOSE_DELAY_MS = 460; // un poco mas que --mcp-nav-duration (420 ms)

export function ViewSliderProvider({
  viewKey = 'default',
  suspended = false,
  width = 'clamp(320px, 38vw, 520px)',
  children
}) {
  const [openForView, setOpenForView] = useState(null);
  // Estado por vista: al cambiar de vista el slider vuelve a estar cerrado y no reaparece
  // abierto al regresar (ajuste de estado durante el render, patron oficial de React).
  const [lastViewKey, setLastViewKey] = useState(viewKey);
  if (lastViewKey !== viewKey) {
    setLastViewKey(viewKey);
    setOpenForView(null);
  }
  const [registry, setRegistry] = useState({}); // id -> { title, subtitle, hasContent, testId }
  const [bodyEl, setBodyEl] = useState(null);
  // Boton de apertura: si una vista monta <ViewSliderTrigger> (p. ej. en su barra superior) el
  // host NO dibuja la pestana pegada al borde; sin trigger externo queda la pestana de siempre.
  const [triggerCount, setTriggerCount] = useState(0);
  const panelId = useId();
  const tabRef = useRef(null);
  const triggerRef = useRef(null);

  const isOpen = !suspended && openForView === viewKey;

  const open = useCallback(() => setOpenForView(viewKey), [viewKey]);
  const close = useCallback(() => setOpenForView(null), []);
  const toggle = useCallback(
    () => setOpenForView(prev => (prev === viewKey ? null : viewKey)),
    [viewKey]
  );

  const register = useCallback((id, meta) => {
    setRegistry(prev => ({ ...prev, [id]: meta }));
  }, []);
  const unregister = useCallback((id) => {
    setRegistry(prev => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);
  const registerTrigger = useCallback(() => {
    setTriggerCount(n => n + 1);
    return () => setTriggerCount(n => Math.max(0, n - 1));
  }, []);

  // El titulo y el testid los manda el ultimo contenido registrado.
  const metas = Object.values(registry);
  const meta = metas.length ? metas[metas.length - 1] : null;
  const hasContent = metas.some(m => m.hasContent);
  const panelTitle = meta?.title || 'Herramientas de la vista';

  const value = useMemo(
    () => ({
      isOpen, open, close, toggle, register, unregister, bodyEl,
      registerTrigger, panelId, triggerRef, suspended, panelTitle
    }),
    [isOpen, open, close, toggle, register, unregister, bodyEl,
      registerTrigger, panelId, suspended, panelTitle]
  );

  return (
    <ViewSliderContext.Provider value={value}>
      {children}
      <ViewSliderHost
        isOpen={isOpen}
        suspended={suspended}
        onClose={close}
        onToggle={toggle}
        meta={meta}
        hasContent={hasContent}
        width={width}
        bodyRef={setBodyEl}
        panelId={panelId}
        tabRef={tabRef}
        triggerRef={triggerRef}
        externalTrigger={triggerCount > 0}
      />
    </ViewSliderContext.Provider>
  );
}

export function useViewSlider() {
  const ctx = useContext(ViewSliderContext);
  return useMemo(
    () => ({
      isOpen: ctx?.isOpen ?? false,
      open: ctx?.open ?? (() => {}),
      close: ctx?.close ?? (() => {}),
      toggle: ctx?.toggle ?? (() => {})
    }),
    [ctx]
  );
}

/**
 * Boton compacto que abre/cierra el slider de la vista activa. Se monta donde la vista quiera
 * (p. ej. al final de la barra superior, junto a la busqueda). Mientras haya uno montado, el
 * host no dibuja la pestana pegada al borde derecho.
 */
export function ViewSliderTrigger({ className = '' }) {
  const ctx = useContext(ViewSliderContext);
  const registerTrigger = ctx?.registerTrigger;
  useEffect(() => (registerTrigger ? registerTrigger() : undefined), [registerTrigger]);
  if (!ctx) return null;
  const label = ctx.isOpen
    ? `Cerrar panel lateral: ${ctx.panelTitle}`
    : `Abrir panel lateral: ${ctx.panelTitle}`;
  return (
    <button
      ref={ctx.triggerRef}
      type="button"
      className={`mcp-view-slider-trigger ${className}`.trim()}
      data-testid="view-slider-tab"
      aria-label={label}
      aria-expanded={ctx.isOpen}
      aria-controls={ctx.panelId}
      title={label}
      onClick={ctx.toggle}
      style={{ visibility: ctx.suspended ? 'hidden' : 'visible' }}
    >
      <ChevronLeft size={16} strokeWidth={2.6} className="mcp-view-slider-chevron" />
    </button>
  );
}

/**
 * Contenido del slider para la vista que lo renderiza. `title` es obligatorio en la
 * practica (es el nombre accesible del panel). Sin hijos registra un slot vacio con titulo.
 */
export function ViewSliderContent({ title, subtitle, 'data-testid': testId, children }) {
  const ctx = useContext(ViewSliderContext);
  const id = useId();
  const register = ctx?.register;
  const unregister = ctx?.unregister;
  const hasContent = React.Children.toArray(children).length > 0;

  useEffect(() => {
    if (!register) return undefined;
    register(id, { title, subtitle, testId, hasContent });
    return () => unregister(id);
  }, [register, unregister, id, title, subtitle, testId, hasContent]);

  if (!ctx?.bodyEl || !hasContent) return null;
  return createPortal(children, ctx.bodyEl);
}

function ViewSliderHost({
  isOpen, suspended, onClose, onToggle, meta, hasContent, width, bodyRef,
  panelId, tabRef, triggerRef, externalTrigger
}) {
  const closeBtnRef = useRef(null);
  const panelRef = useRef(null);
  const scrollRef = useRef(null);
  const wasOpenRef = useRef(false);
  const [closedDone, setClosedDone] = useState(true);

  const title = meta?.title || 'Herramientas de la vista';
  const subtitle = meta?.subtitle;
  const testId = meta?.testId || 'view-slider';

  // visibility:hidden solo cuando el cierre ya termino su animacion (saca el contenido del
  // orden de tabulacion sin cortar la animacion de salida).
  useEffect(() => {
    if (isOpen) {
      setClosedDone(false);
      return undefined;
    }
    const t = setTimeout(() => setClosedDone(true), CLOSE_DELAY_MS);
    return () => clearTimeout(t);
  }, [isOpen]);

  // Al abrir: scroll interno al inicio y foco al panel. Al cerrar: foco de vuelta a la
  // pestana si el foco estaba dentro del panel.
  useEffect(() => {
    if (isOpen) {
      if (scrollRef.current) scrollRef.current.scrollTop = 0;
      closeBtnRef.current?.focus();
    } else if (wasOpenRef.current) {
      const active = document.activeElement;
      if (!active || active === document.body || panelRef.current?.contains(active)) {
        (triggerRef.current || tabRef.current)?.focus();
      }
    }
    wasOpenRef.current = isOpen;
  }, [isOpen]);

  // Esc cierra.
  useEffect(() => {
    if (!isOpen) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape' && !e.defaultPrevented) {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  // Trampa de foco simple mientras esta abierto (Tab / Shift+Tab ciclan dentro del panel).
  // Listener NATIVO en el panel: el contenido entra por portal, y los eventos sinteticos de
  // React de un portal burbujean por el arbol React (la vista), no por el DOM del panel.
  useEffect(() => {
    const panel = panelRef.current;
    if (!isOpen || !panel) return undefined;
    const onKeyDown = (e) => {
      if (e.key !== 'Tab') return;
      const focusables = panel.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    panel.addEventListener('keydown', onKeyDown);
    return () => panel.removeEventListener('keydown', onKeyDown);
  }, [isOpen]);

  const panelVisible = isOpen || !closedDone;
  const tabLabel = isOpen ? `Cerrar panel lateral: ${title}` : `Abrir panel lateral: ${title}`;

  return (
    <>
      <div
        className="mcp-view-slider-backdrop"
        data-testid={`${testId}-backdrop`}
        data-state={isOpen ? 'open' : 'closed'}
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        className="mcp-view-slider"
        data-testid={testId}
        data-state={isOpen ? 'open' : 'closed'}
        style={{ width, visibility: suspended ? 'hidden' : 'visible' }}
      >
        {!externalTrigger && (
          <button
            ref={tabRef}
            type="button"
            className="mcp-view-slider-tab"
            data-testid="view-slider-tab"
            aria-label={tabLabel}
            aria-expanded={isOpen}
            aria-controls={panelId}
            title={tabLabel}
            onClick={onToggle}
          >
            <ChevronLeft size={14} className="mcp-view-slider-chevron" />
          </button>
        )}
        <aside
          id={panelId}
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          aria-hidden={!isOpen}
          data-testid="view-slider-panel"
          className="mcp-view-slider-panel"
          style={{ visibility: panelVisible ? 'inherit' : 'hidden' }}
        >
          <div className="mcp-view-slider-header">
            <div style={{ minWidth: 0 }}>
              <h3 style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>{title}</h3>
              {subtitle && (
                <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{subtitle}</span>
              )}
            </div>
            <button
              ref={closeBtnRef}
              type="button"
              className="mcp-view-slider-close"
              onClick={onClose}
              aria-label="Cerrar panel"
            >
              <X size={18} />
            </button>
          </div>
          <div ref={scrollRef} className="mcp-view-slider-scroll">
            <div ref={bodyRef} className="mcp-view-slider-body" />
            {!hasContent && (
              <p className="mcp-view-slider-empty" data-testid="view-slider-empty">
                Esta vista todavia no tiene herramientas en el panel lateral.
              </p>
            )}
          </div>
        </aside>
      </div>
    </>
  );
}
