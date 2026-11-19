import React, { useState, useCallback } from 'react';
import { X } from 'lucide-react';
import { ViewSliderContent, useViewSlider } from './ViewSlider';
import { EmptyTerminalSlot, TerminalPane } from './terminals/TerminalPane';
import { ExternalUrlConfirmDialog } from './terminals/ExternalUrlConfirmDialog';
import { MatrixBanners } from './terminals/MatrixBanners';
import { MatrixTopRegion } from './terminals/MatrixTopRegion';
import { useInstallFlow } from './terminals/useInstallFlow';
import { usePaneActions } from './terminals/usePaneActions';
import { createInitialPaneState, useMatrixState } from './terminals/useMatrixState';
import { TerminalSliderControls } from './terminals/TerminalSliderControls';
import { RelayPanel } from './relay/RelayPanel';
import { useRelayState } from './relay/useRelayState';

// Compatibilidad: estos nombres se exportaban desde este modulo antes de partirlo.
export { MATRIX_PANE_IDS, ROLE_OPTIONS, stripAnsiSequences } from './terminals/terminalModel';

/**
 * `active` = la vista de terminales esta a la vista. ProjectWorkspace la mantiene montada (oculta)
 * al cambiar de pestana para que el sondeo y las instancias de xterm sigan vivas; mientras esta
 * oculta no registra su contenido en el slider y sondea despacio.
 */
export function SwarmTerminalMatrix({ api, project, searchQuery = '', active = true, onOpenSettings }) {
  const { close: closeSlider, open: openSlider, isOpen: sliderOpen } = useViewSlider();
  const [cwdInput, setCwdInput] = useState('');
  const [roleInput, setRoleInput] = useState('implementer');
  const [copiedKey, setCopiedKey] = useState(null);
  const [pendingExternalUrl, setPendingExternalUrl] = useState(null);
  const [pathEditRequest, setPathEditRequest] = useState({ cliId: null, nonce: 0 });

  // Relevo entre agentes (F1.6): bandeja del humano, auditoria y paradas; solo sondea con la vista a la vista.
  const relay = useRelayState(api, project?.project_uuid, { enabled: active });

  const {
    panes,
    setPanes,
    panesRef,
    focusedPaneId,
    focusRequest,
    matrixStatus,
    setMatrixStatus,
    catalog,
    setCatalog,
    catalogRef,
    catalogById,
    maxPanes,
    notice,
    setNotice,
    showNotice,
    pendingNativeConfirmations,
    runWithNativeDialogNotice,
    mountedRef,
    apiRef,
    pollersRef,
    refreshMatrixStatus,
    refreshCatalog,
    refreshAll,
    openPaneIds,
    hiddenPaneCount,
    requestTerminalFocus,
    handleClickPane,
    registerTerminalRef,
    paneLabels,
    visiblePaneSet,
    layout,
    emptySlots,
    noOpenTerminals,
    searchHidesAll,
    statusNotice,
    activePanes,
    relayTargets
  } = useMatrixState({ api, project, searchQuery, active });

  const handleCopy = useCallback(async (key, text) => {
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      }
      if (mountedRef.current) {
        setCopiedKey(key);
        setTimeout(() => {
          if (mountedRef.current) {
            setCopiedKey((prev) => (prev === key ? null : prev));
          }
        }, 1800);
      }
    } catch {
      // Ignorar si clipboard no está disponible en entorno restringido
    }
  }, [mountedRef]);

  // Seleccionar una terminal en el slider la enfoca y deja el teclado listo para escribir.
  const handleFocusFromSlider = useCallback((paneId) => {
    requestTerminalFocus(paneId);
    closeSlider();
  }, [requestTerminalFocus, closeSlider]);

  // ---- Acciones sobre terminales ------------------------------------------------------

  const {
    launchingCliId,
    unregisterAgyBusy,
    resettingBreaker,
    trippedCases,
    markPaneOpened,
    handleLaunchCli,
    handlePausePane,
    handleClosePane,
    handleResizePane,
    sendPaneInput,
    handleSetMaxPanes,
    handleSetCliPath,
    handleTogglePaneAutoRelay,
    handleTogglePaneWorkerMode,
    handleRegisterAgyMcp,
    handleUnregisterAgyMcp,
    handleResetLoopBreaker
  } = usePaneActions({
    api,
    apiRef,
    project,
    panesRef,
    setPanes,
    createInitialPaneState,
    catalogRef,
    setCatalog,
    matrixStatus,
    setMatrixStatus,
    mountedRef,
    pollersRef,
    showNotice,
    setNotice,
    runWithNativeDialogNotice,
    requestTerminalFocus,
    closeSlider,
    refreshCatalog,
    refreshMatrixStatus,
    cwdInput,
    roleInput
  });

  const {
    installPlan,
    installBusyCliId,
    discardInstallPlan,
    handlePrepareInstall,
    handleConfirmInstall
  } = useInstallFlow({
    api,
    mountedRef,
    catalogRef,
    setNotice,
    showNotice,
    runWithNativeDialogNotice,
    markPaneOpened,
    requestTerminalFocus,
    closeSlider,
    refreshMatrixStatus
  });

  const handleNoticeAction = useCallback((action) => {
    if (action.kind === 'install') {
      void handlePrepareInstall(action.cliId);
    } else if (action.kind === 'configure_path') {
      setPathEditRequest((prev) => ({ cliId: action.cliId, nonce: prev.nonce + 1 }));
    }
  }, [handlePrepareInstall]);


  return (
    <div
      data-testid="swarm-terminal-matrix"
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        width: '100%',
        minHeight: 0,
        overflow: 'hidden'
      }}
    >
      {/* Todo el control vive en el slider lateral derecho (seccion 4 del plan). */}
      {active && (
      <ViewSliderContent
        title="Terminales"
        subtitle="Controles del enjambre"
        data-testid="terminals-slider"
      >
        <TerminalSliderControls
          status={matrixStatus}
          autoRelay={{ available: Boolean(api?.pty_set_auto_relay) }}
          onToggleAutoRelay={(paneId) => void handleTogglePaneAutoRelay(paneId)}
          workerMode={{ available: Boolean(api?.pty_set_worker_mode) }}
          onToggleWorkerMode={api?.pty_set_worker_mode ? (paneId) => void handleTogglePaneWorkerMode(paneId) : undefined}
          relaySection={
            <RelayPanel api={api} projectUuid={project?.project_uuid} relay={relay} targets={relayTargets} />
          }
          hiddenPaneCount={hiddenPaneCount}
          statusNotice={statusNotice}
          catalog={catalog}
          maxPanes={maxPanes}
          hardMaxPanes={catalog.hardMaxPanes}
          activePanes={activePanes}
          focusedPaneId={focusedPaneId}
          paneLabels={paneLabels}
          catalogById={catalogById}
          notice={notice}
          onDismissNotice={() => setNotice(null)}
          onNoticeAction={handleNoticeAction}
          cwdValue={cwdInput}
          onCwdChange={setCwdInput}
          roleValue={roleInput}
          onRoleChange={setRoleInput}
          launchingCliId={launchingCliId}
          copiedKey={copiedKey}
          onCopy={handleCopy}
          onRefresh={refreshAll}
          onLaunch={handleLaunchCli}
          onFocusPane={handleFocusFromSlider}
          onPausePane={handlePausePane}
          onClosePane={handleClosePane}
          onSetMaxPanes={handleSetMaxPanes}
          installPlan={installPlan}
          installBusyCliId={installBusyCliId}
          onPrepareInstall={handlePrepareInstall}
          onConfirmInstall={handleConfirmInstall}
          onCancelInstall={discardInstallPlan}
          onSetCliPath={handleSetCliPath}
          pathEditRequest={pathEditRequest}
          onOpenSettings={onOpenSettings}
        />
      </ViewSliderContent>
      )}

      <MatrixTopRegion
        api={api}
        projectUuid={project?.project_uuid}
        onRestored={refreshAll}
        panes={panes}
        catalogById={catalogById}
        relay={relay}
        onOpenSettings={onOpenSettings}
      />

      <MatrixBanners
        pendingNativeConfirmations={pendingNativeConfirmations}
        trippedCases={trippedCases}
        resettingBreaker={resettingBreaker}
        canResetBreaker={Boolean(api?.reset_loop_breaker)}
        onResetLoopBreaker={() => void handleResetLoopBreaker()}
      />

      <div
        data-testid="matrix-grid"
        data-layout={layout.id}
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: layout.columns,
          gridTemplateRows: layout.rowTemplate,
          gap: '10px'
        }}
      >
        {searchHidesAll && (
          <div
            style={{
              gridColumn: '1 / -1',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '28px',
              borderRadius: 'var(--radius-lg)',
              border: '1px dashed var(--border-subtle)',
              color: 'var(--text-muted)',
              fontSize: '12.5px'
            }}
          >
            Ninguna terminal coincide con «{searchQuery}».
          </div>
        )}
        {/* D7: todos los paneles abiertos siguen montados; los de otros proyectos (o fuera de la busqueda)
            solo se ocultan, sin cerrar su proceso ni perder su pantalla. */}
        {openPaneIds.map((paneId) => (
          <TerminalPane
            key={paneId}
            api={api}
            hidden={!visiblePaneSet.has(paneId)}
            pane={panes[paneId]}
            label={paneLabels[paneId]}
            cli={catalogById[panes[paneId].cliProfile]}
            isFocused={focusedPaneId === paneId}
            focusToken={focusRequest.paneId === paneId ? focusRequest.nonce : 0}
            onFocus={handleClickPane}
            onInput={sendPaneInput}
            onResize={handleResizePane}
            onRequestOpenUrl={setPendingExternalUrl}
            onRegisterAgyMcp={api?.pty_register_agy_mcp ? () => void handleRegisterAgyMcp() : undefined}
            onUnregisterAgyMcp={api?.pty_unregister_agy_mcp ? () => void handleUnregisterAgyMcp() : undefined}
            unregisterAgyBusy={unregisterAgyBusy}
            registerTerminalRef={registerTerminalRef}
            relayPaused={relay.pausedPanes.includes(panes[paneId].nodeUuid)}
            globalPaused={relay.globalPaused}
            onOpenSettings={onOpenSettings}
          />
        ))}
        {!searchHidesAll && Array.from({ length: emptySlots }, (_, i) => (
          <EmptyTerminalSlot key={`empty-${i}`} index={i} showHint={noOpenTerminals && i === 0} onAdd={openSlider} />
        ))}
      </div>

      {/* Aviso discreto cuando el slider esta cerrado; el detalle completo vive en el slider. */}
      {notice && !sliderOpen && (
        <div
          role="status"
          data-testid="matrix-notice"
          data-code={notice.code}
          style={{
            position: 'absolute',
            left: '50%',
            bottom: '12px',
            transform: 'translateX(-50%)',
            maxWidth: 'min(560px, 92%)',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '6px 10px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            boxShadow: 'var(--shadow-md)',
            fontSize: '11.5px',
            color: 'var(--text-primary)'
          }}
        >
          <span>{notice.title}</span>
          <button
            type="button"
            data-testid="matrix-notice-open"
            onClick={() => openSlider()}
            style={{ border: 'none', background: 'transparent', color: 'var(--accent-primary)', fontWeight: 700, cursor: 'pointer', fontSize: '11.5px', padding: 0 }}
          >
            Ver detalle
          </button>
          <button
            type="button"
            aria-label="Descartar aviso"
            data-testid="matrix-notice-dismiss"
            onClick={() => setNotice(null)}
            style={{ border: 'none', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', display: 'inline-flex', padding: 0 }}
          >
            <X size={13} />
          </button>
        </div>
      )}

      <ExternalUrlConfirmDialog
        url={pendingExternalUrl}
        onCancel={() => setPendingExternalUrl(null)}
        onConfirm={() => {
          const target = pendingExternalUrl;
          setPendingExternalUrl(null);
          if (typeof window !== 'undefined' && typeof window.open === 'function') {
            window.open(target, '_blank', 'noopener,noreferrer');
          }
        }}
      />
    </div>
  );
}
