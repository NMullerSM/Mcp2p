import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  Info,
  Pause,
  Play,
  RefreshCw,
  Settings2,
  Square,
  X
} from 'lucide-react';
import { CliLogo } from './CliLogo';
import {
  canOfferInstall,
  describeInstallBlock,
  DEFAULT_MANUAL_COMMAND,
  DEFAULT_MCP_CONFIG,
  HARD_MAX_PANES,
  ROLE_OPTIONS,
  SIZE_PRESETS
} from './terminalModel';

/**
 * Todo el control de las terminales vive aqui (slider lateral derecho). Es presentacional:
 * el estado y las llamadas al puente son de SwarmTerminalMatrix.
 */

const btnBase = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '5px',
  padding: '5px 10px',
  fontSize: '11.5px',
  fontWeight: 600,
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--bg-subtle)',
  color: 'var(--text-primary)',
  cursor: 'pointer'
};
const btnPrimary = {
  ...btnBase,
  border: 'none',
  backgroundColor: 'var(--accent-primary)',
  color: '#ffffff',
  fontWeight: 700
};
const chip = {
  padding: '1px 6px',
  borderRadius: 'var(--radius-sm)',
  fontFamily: 'var(--font-mono)',
  fontSize: '10px',
  backgroundColor: 'var(--bg-subtle)',
  color: 'var(--text-secondary)',
  border: '1px solid var(--border-subtle)'
};
const fieldStyle = {
  height: '28px',
  padding: '0 9px',
  borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--bg-app)',
  color: 'var(--text-primary)',
  fontFamily: 'var(--font-mono)',
  fontSize: '11.5px',
  outline: 'none',
  minWidth: 0
};
const sectionTitle = {
  margin: 0,
  fontSize: '11px',
  fontWeight: 700,
  letterSpacing: '0.04em',
  textTransform: 'uppercase',
  color: 'var(--text-secondary)'
};
const mutedText = { fontSize: '11.5px', color: 'var(--text-muted)', lineHeight: 1.4 };

const TONES = {
  error: { color: 'var(--accent-danger)', bg: 'rgba(239, 68, 68, 0.1)', border: 'rgba(239, 68, 68, 0.35)', Icon: AlertTriangle },
  warning: { color: 'var(--accent-warning)', bg: 'rgba(245, 158, 11, 0.1)', border: 'rgba(245, 158, 11, 0.38)', Icon: AlertTriangle },
  info: { color: 'var(--text-secondary)', bg: 'var(--bg-subtle)', border: 'var(--border-subtle)', Icon: Info }
};

function CopyBlock({ testId, label, text, copiedKey, onCopy }) {
  const key = `${testId}`;
  const copied = copiedKey === key;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <span style={{ fontSize: '10.5px', fontWeight: 600, color: 'var(--text-secondary)' }}>{label}</span>
        <button
          type="button"
          data-testid={`${testId}-copy`}
          onClick={() => onCopy(key, text)}
          style={{ ...btnBase, padding: '2px 7px', fontSize: '10.5px', color: copied ? 'var(--accent-success)' : 'var(--text-primary)' }}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          <span>{copied ? 'Copiado' : 'Copiar'}</span>
        </button>
      </div>
      <pre
        data-testid={testId}
        style={{
          margin: 0,
          padding: '6px 8px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: 'var(--bg-app)',
          border: '1px solid var(--border-subtle)',
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
          color: 'var(--text-primary)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          userSelect: 'text'
        }}
      >
        {text}
      </pre>
    </div>
  );
}

/** Comando manual y configuracion mcpServers para conectar un CLI externo. */
export function ManualConnectionCard({ testId, manualCommand, mcpConfig, copiedKey, onCopy }) {
  return (
    <div
      data-testid={testId}
      style={{
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px'
      }}
    >
      <p style={{ ...mutedText, margin: 0 }}>
        Para usar un CLI en una terminal propia, ejecútalo y conéctalo al servidor MCP:
      </p>
      <CopyBlock
        testId={`${testId}-cmd`}
        label="Comando del servidor MCP"
        text={manualCommand || DEFAULT_MANUAL_COMMAND}
        copiedKey={copiedKey}
        onCopy={onCopy}
      />
      <CopyBlock
        testId={`${testId}-config`}
        label="Configuración JSON (mcpServers)"
        text={JSON.stringify(mcpConfig || DEFAULT_MCP_CONFIG, null, 2)}
        copiedKey={copiedKey}
        onCopy={onCopy}
      />
    </div>
  );
}

/** Causa exacta del fallo y que hacer. `notice` sale de describeTerminalError. */
export function TerminalNotice({ notice, onDismiss, onAction, copiedKey, onCopy, testId = 'slider-notice' }) {
  if (!notice) return null;
  const tone = TONES[notice.tone] || TONES.error;
  const ToneIcon = tone.Icon;
  return (
    <div
      role="alert"
      data-testid={testId}
      data-code={notice.code}
      style={{
        backgroundColor: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 'var(--radius-md)',
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: '7px'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '7px', color: tone.color, fontSize: '12.5px', fontWeight: 700 }}>
          <ToneIcon size={14} style={{ marginTop: '2px', flexShrink: 0 }} />
          <span data-testid={`${testId}-title`}>{notice.title}</span>
        </div>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
          <span style={{ ...chip, color: tone.color }}>{notice.code}</span>
          {onDismiss && (
            <button
              type="button"
              aria-label="Descartar aviso"
              data-testid={`${testId}-dismiss`}
              onClick={onDismiss}
              style={{ border: 'none', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', display: 'inline-flex', padding: 0 }}
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>
      {notice.message && (
        <p data-testid={`${testId}-message`} style={{ margin: 0, fontSize: '12px', color: 'var(--text-primary)', lineHeight: 1.45 }}>
          {notice.message}
        </p>
      )}
      {notice.hint && (
        <p data-testid={`${testId}-hint`} style={{ margin: 0, fontSize: '11.5px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
          {notice.hint}
        </p>
      )}
      {notice.manualCommand && (
        <ManualConnectionCard
          testId={`${testId}-manual`}
          manualCommand={notice.manualCommand}
          mcpConfig={notice.mcpConfig}
          copiedKey={copiedKey}
          onCopy={onCopy}
        />
      )}
      {notice.installCommand && (
        <CopyBlock
          testId={`${testId}-install-command`}
          label="Comando de instalación (ejecútalo tú en tu terminal)"
          text={notice.installCommand}
          copiedKey={copiedKey}
          onCopy={onCopy}
        />
      )}
      {onAction && notice.actions?.some((a) => a.kind === 'install' || a.kind === 'configure_path') && (
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {notice.actions.filter((a) => a.kind === 'install' || a.kind === 'configure_path').map((action) => (
            <button
              key={`${action.kind}-${action.cliId}`}
              type="button"
              data-testid={`${testId}-action-${action.kind}`}
              onClick={() => onAction(action)}
              style={btnBase}
            >
              {action.kind === 'install' ? 'Revisar instalación' : 'Configurar ruta'}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function InstallConfirmation({ cliId, plan, busy, onConfirm, onCancel }) {
  const [acknowledged, setAcknowledged] = useState(false);
  const needsAck = Boolean(plan.downloadsScript);
  const canConfirm = !busy && (!needsAck || acknowledged);
  return (
    <div
      data-testid={`install-confirm-${cliId}`}
      role="group"
      aria-label={`Confirmar la instalación de ${plan.displayName}`}
      style={{
        border: '1px solid rgba(245, 158, 11, 0.45)',
        borderRadius: 'var(--radius-md)',
        backgroundColor: 'rgba(245, 158, 11, 0.07)',
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px'
      }}
    >
      <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
        Instalar {plan.displayName}
      </div>
      <p style={{ ...mutedText, margin: 0 }}>
        Se ejecutará exactamente este comando, dentro de una terminal visible:
      </p>
      <pre
        data-testid="install-command"
        style={{
          margin: 0,
          padding: '6px 8px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: 'var(--bg-app)',
          border: '1px solid var(--border-subtle)',
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
          color: 'var(--text-primary)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          userSelect: 'text'
        }}
      >
        {plan.command}
      </pre>
      <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
        {plan.method && <span data-testid="install-method" style={chip}>método: {plan.method}</span>}
        {plan.platform && <span style={chip}>{plan.platform}</span>}
        {plan.requires.map((req) => (
          <span key={req} data-testid="install-requires" style={chip}>requiere {req}</span>
        ))}
      </div>
      {needsAck && (
        <div
          data-testid="install-script-warning"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            padding: '7px 9px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid rgba(239, 68, 68, 0.4)',
            backgroundColor: 'rgba(239, 68, 68, 0.08)',
            fontSize: '11.5px',
            color: 'var(--text-primary)',
            lineHeight: 1.45
          }}
        >
          <span style={{ fontWeight: 700, color: 'var(--accent-danger)' }}>
            Este instalador descarga un script de Internet y lo ejecuta en tu shell.
          </span>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: '6px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              data-testid="install-script-ack"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              style={{ marginTop: '2px' }}
            />
            <span>Revisé el comando y la fuente, y acepto ejecutar un script descargado.</span>
          </label>
        </div>
      )}
      {plan.warning && (
        <p data-testid="install-warning" style={{ ...mutedText, margin: 0, color: 'var(--accent-warning)' }}>{plan.warning}</p>
      )}
      {plan.sourceUrl && (
        <span data-testid="install-source" style={{ ...mutedText, fontFamily: 'var(--font-mono)', fontSize: '10.5px', wordBreak: 'break-all' }}>
          Fuente: {plan.sourceUrl}
        </span>
      )}
      {plan.note && <span style={mutedText}>{plan.note}</span>}
      <span style={mutedText}>La confirmación vence a los {Math.round(plan.expiresInS)} s y sirve una sola vez.</span>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
        <button type="button" data-testid="install-cancel-btn" onClick={onCancel} style={btnBase}>
          Cancelar
        </button>
        <button
          type="button"
          data-testid="install-confirm-btn"
          disabled={!canConfirm}
          onClick={onConfirm}
          style={{ ...btnPrimary, opacity: canConfirm ? 1 : 0.5, cursor: canConfirm ? 'pointer' : 'not-allowed' }}
        >
          {busy ? 'Instalando...' : 'Confirmar e instalar'}
        </button>
      </div>
    </div>
  );
}

function BinaryPathEditor({ cli, onSetCliPath }) {
  const [value, setValue] = useState(cli.configured_path || '');
  const [saving, setSaving] = useState(false);
  const save = async (next) => {
    setSaving(true);
    try {
      await onSetCliPath(cli.id, next);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div data-testid={`cli-path-editor-${cli.id}`} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '11px', color: 'var(--text-secondary)' }}>
        <span>Ruta del binario de {cli.display_name}</span>
        <input
          type="text"
          data-testid={`cli-path-input-${cli.id}`}
          spellCheck={false}
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="/ruta/absoluta/al/ejecutable"
          style={{ ...fieldStyle, width: '100%', boxSizing: 'border-box' }}
        />
      </label>
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        <button
          type="button"
          data-testid={`cli-path-save-${cli.id}`}
          disabled={saving}
          onClick={() => save(value.trim())}
          style={btnBase}
        >
          {value.trim() ? 'Guardar ruta' : 'Quitar ruta'}
        </button>
        {cli.configured_path && (
          <button
            type="button"
            data-testid={`cli-path-clear-${cli.id}`}
            disabled={saving}
            onClick={() => { setValue(''); void save(''); }}
            style={btnBase}
          >
            Quitar ruta configurada
          </button>
        )}
      </div>
      <span style={mutedText}>El backend valida que sea un archivo ejecutable. Vacío borra la ruta y vuelve a buscar en el PATH.</span>
    </div>
  );
}

function CliRow({
  cli,
  platformSupported,
  launching,
  anyLaunching,
  onLaunch,
  installPlan,
  installBusy,
  onPrepareInstall,
  onConfirmInstall,
  onCancelInstall,
  onSetCliPath,
  pathEditRequest
}) {
  const installed = Boolean(cli.installed);
  const launchable = cli.launchable !== false && installed;
  const unverified = cli.verified === false || cli.source === 'user';
  const offerInstall = canOfferInstall(cli);
  const installBlock = describeInstallBlock(cli);
  const [showPath, setShowPath] = useState(false);
  const requestedNonce = pathEditRequest?.cliId === cli.id ? pathEditRequest.nonce : 0;
  useEffect(() => {
    if (requestedNonce) setShowPath(true);
  }, [requestedNonce]);
  const planHere = installPlan && installPlan.cliId === cli.id ? installPlan : null;
  const pathInvalid = cli.configured_path && cli.configured_path_valid === false;

  return (
    <li
      data-testid={`cli-row-${cli.id}`}
      style={{
        listStyle: 'none',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        padding: '9px 11px',
        display: 'flex',
        flexDirection: 'column',
        gap: '7px',
        backgroundColor: 'var(--bg-card)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flexWrap: 'wrap' }}>
          <CliLogo cliId={cli.id} icon={cli.icon} name={cli.display_name} size={16} muted={false} />
          <span style={{ fontSize: '12.5px', fontWeight: 700, color: 'var(--text-primary)' }}>{cli.display_name}</span>
          <span
            data-testid={`cli-status-${cli.id}`}
            style={{
              ...chip,
              color: installed ? 'var(--accent-success)' : 'var(--accent-warning)'
            }}
          >
            {installed ? 'Instalado' : 'No instalado'}
          </span>
          {unverified && (
            <span style={{ ...chip, color: 'var(--accent-warning)' }} title="Viene del catálogo del usuario">
              No verificado
            </span>
          )}
        </div>
        <div style={{ display: 'inline-flex', gap: '6px', flexShrink: 0 }}>
          {offerInstall && (
            <button
              type="button"
              data-testid={`cli-install-${cli.id}`}
              disabled={installBusy || Boolean(planHere)}
              onClick={() => onPrepareInstall(cli.id)}
              style={{ ...btnBase, opacity: installBusy || planHere ? 0.5 : 1 }}
            >
              <Download size={11} />
              <span>{installBusy ? 'Preparando...' : 'Instalar'}</span>
            </button>
          )}
          <button
            type="button"
            data-testid={`cli-open-${cli.id}`}
            disabled={!launchable || !platformSupported || anyLaunching}
            onClick={() => onLaunch(cli.id)}
            style={{
              ...btnPrimary,
              opacity: !launchable || !platformSupported || anyLaunching ? 0.5 : 1,
              cursor: !launchable || !platformSupported ? 'not-allowed' : (anyLaunching ? 'wait' : 'pointer')
            }}
          >
            <Play size={11} />
            <span>{launching ? 'Abriendo...' : 'Abrir'}</span>
          </button>
        </div>
      </div>

      {installed && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
          {cli.version && <span data-testid={`cli-version-${cli.id}`} style={chip}>{cli.version}</span>}
          {cli.resolution_source && <span data-testid={`cli-source-${cli.id}`} style={chip}>{cli.resolution_source}</span>}
          <span
            data-testid={`cli-path-${cli.id}`}
            title={cli.resolved_path || ''}
            style={{ ...mutedText, fontFamily: 'var(--font-mono)', fontSize: '10.5px', wordBreak: 'break-all' }}
          >
            {cli.resolved_path}
          </span>
        </div>
      )}

      {!installed && !offerInstall && installBlock && (
        <p data-testid={`cli-install-block-${cli.id}`} style={{ ...mutedText, margin: 0 }}>
          No se ofrece instalar: {installBlock}
        </p>
      )}

      {pathInvalid && (
        <p data-testid={`cli-path-invalid-${cli.id}`} style={{ ...mutedText, margin: 0, color: 'var(--accent-warning)' }}>
          La ruta configurada ({cli.configured_path}) ya no es válida.
        </p>
      )}

      {planHere && (
        <InstallConfirmation
          cliId={cli.id}
          plan={planHere}
          busy={installBusy}
          onConfirm={onConfirmInstall}
          onCancel={onCancelInstall}
        />
      )}

      {cli.kind !== 'shell' && (
        <div>
          <button
            type="button"
            data-testid={`cli-path-toggle-${cli.id}`}
            aria-expanded={showPath}
            onClick={() => setShowPath((prev) => !prev)}
            style={{ ...btnBase, padding: '3px 8px', fontSize: '11px', backgroundColor: 'transparent' }}
          >
            <Settings2 size={11} />
            <span>{showPath ? 'Ocultar ruta del binario' : 'Ruta del binario'}</span>
          </button>
        </div>
      )}
      {showPath && cli.kind !== 'shell' && <BinaryPathEditor cli={cli} onSetCliPath={onSetCliPath} />}
    </li>
  );
}

function ActiveTerminalRow({ pane, cli, label, isFocused, onFocusPane, onPausePane, onClosePane, onResizePane }) {
  const paneId = pane.paneId;
  const sizeValue = `${pane.cols}x${pane.rows}`;
  const knownSize = SIZE_PRESETS.some((p) => p.label === sizeValue);
  const stateLabel = pane.alive ? (pane.paused ? 'Pausado' : 'Activo') : 'Finalizado';
  const stateColor = pane.alive ? (pane.paused ? 'var(--accent-warning)' : 'var(--accent-success)') : 'var(--text-muted)';
  return (
    <li
      data-testid={`active-row-${paneId}`}
      style={{
        listStyle: 'none',
        border: `1px solid ${isFocused ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
        borderRadius: 'var(--radius-md)',
        padding: '9px 11px',
        display: 'flex',
        flexDirection: 'column',
        gap: '7px',
        backgroundColor: 'var(--bg-card)'
      }}
    >
      <button
        type="button"
        data-testid={`pane-focus-btn-${paneId}`}
        aria-label={`Enfocar ${label}`}
        onClick={() => onFocusPane(paneId)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          width: '100%',
          padding: 0,
          border: 'none',
          background: 'transparent',
          color: 'var(--text-primary)',
          cursor: 'pointer',
          textAlign: 'left'
        }}
      >
        <CliLogo cliId={pane.cliProfile} icon={cli?.icon} name={cli?.display_name || pane.cliProfile} size={16} muted={false} />
        <span style={{ fontSize: '12.5px', fontWeight: 700 }}>{label}</span>
        <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>{cli?.display_name || pane.cliProfile}</span>
        <span data-testid={`pane-status-${paneId}`} style={{ marginLeft: 'auto', fontSize: '11px', fontWeight: 700, color: stateColor }}>
          {stateLabel}
        </span>
      </button>

      {pane.cwd && (
        <span title={pane.cwd} style={{ ...mutedText, fontFamily: 'var(--font-mono)', fontSize: '10.5px', wordBreak: 'break-all' }}>
          {pane.cwd}
        </span>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: '5px', flexWrap: 'wrap' }}>
        {pane.pid && <span data-testid={`pane-pid-${paneId}`} style={chip}>PID {pane.pid}</span>}
        {(pane.alive || pane.rssMb > 0) && (
          <span data-testid={`pane-rss-${paneId}`} style={chip}>{Number(pane.rssMb || 0).toFixed(1)} MB</span>
        )}
        {pane.nodeUuid && (
          <span data-testid={`pane-node-${paneId}`} title={pane.nodeUuid} style={chip}>{pane.nodeUuid.slice(0, 12)}</span>
        )}
        {pane.registryStatus && <span data-testid={`pane-registry-${paneId}`} style={chip}>{pane.registryStatus}</span>}
        {!pane.alive && pane.exitCode !== null && pane.exitCode !== undefined && (
          <span style={chip}>código {pane.exitCode}</span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
        {pane.alive && (
          <button type="button" data-testid={`pane-pause-btn-${paneId}`} onClick={() => onPausePane(paneId)} style={btnBase}>
            {pane.paused ? <Play size={11} /> : <Pause size={11} />}
            <span>{pane.paused ? 'Reanudar' : 'Pausar'}</span>
          </button>
        )}
        <button
          type="button"
          data-testid={`pane-close-btn-${paneId}`}
          onClick={() => onClosePane(paneId)}
          style={{ ...btnBase, color: pane.alive ? 'var(--accent-danger)' : 'var(--text-secondary)' }}
        >
          <Square size={10} />
          <span>Cerrar</span>
        </button>
        <select
          aria-label={`Tamaño del terminal ${label}`}
          data-testid={`size-select-${paneId}`}
          className="mcp-combobox"
          value={sizeValue}
          onChange={(e) => {
            const [c, r] = e.target.value.split('x').map((n) => parseInt(n, 10));
            if (c > 0 && r > 0) onResizePane(paneId, c, r);
          }}
          style={{ height: '27px', fontSize: '11px', maxWidth: '96px' }}
        >
          {!knownSize && <option value={sizeValue}>{sizeValue}</option>}
          {SIZE_PRESETS.map((preset) => (
            <option key={preset.label} value={preset.label}>{preset.label}</option>
          ))}
        </select>
      </div>
    </li>
  );
}

export function TerminalSliderControls({
  status,
  catalog,
  maxPanes,
  hardMaxPanes = HARD_MAX_PANES,
  activePanes,
  focusedPaneId,
  paneLabels,
  catalogById,
  notice,
  statusNotice,
  onDismissNotice,
  onNoticeAction,
  cwdValue,
  onCwdChange,
  roleValue,
  onRoleChange,
  launchingCliId,
  copiedKey,
  onCopy,
  onRefresh,
  onLaunch,
  onFocusPane,
  onPausePane,
  onClosePane,
  onResizePane,
  onSetMaxPanes,
  installPlan,
  installBusyCliId,
  onPrepareInstall,
  onConfirmInstall,
  onCancelInstall,
  onSetCliPath,
  pathEditRequest
}) {
  const [showManual, setShowManual] = useState(false);

  const hubText = status.hubRunning && status.hubUrl ? `Hub activo: ${status.hubUrl}` : 'Hub en espera';
  const connectorProblem = status.platformUnsupported
    ? 'Terminales integradas no disponibles en este sistema'
    : (status.degraded ? 'Conector de terminales no disponible' : null);
  const platformSupported = catalog.platformSupported !== false && !status.platformUnsupported;
  const maxOptions = Array.from({ length: hardMaxPanes }, (_, i) => i + 1);

  return (
    <div data-testid="terminals-slider-controls" style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <TerminalNotice
        notice={notice}
        onDismiss={onDismissNotice}
        onAction={onNoticeAction}
        copiedKey={copiedKey}
        onCopy={onCopy}
      />

      {/* Estado general del enjambre */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '9px' }}>
        <h4 style={sectionTitle}>Enjambre</h4>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
          <div
            data-testid="hub-status-badge"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              fontSize: '11.5px',
              fontWeight: 600,
              fontFamily: 'var(--font-mono)',
              backgroundColor: connectorProblem ? 'rgba(245, 158, 11, 0.14)' : (status.hubRunning ? 'rgba(var(--accent-primary-rgb), 0.14)' : 'var(--bg-subtle)'),
              color: connectorProblem ? 'var(--accent-warning)' : (status.hubRunning ? 'var(--accent-success)' : 'var(--text-secondary)'),
              border: '1px solid var(--border-subtle)',
              wordBreak: 'break-all'
            }}
          >
            <span
              style={{
                width: '7px',
                height: '7px',
                borderRadius: '50%',
                flexShrink: 0,
                backgroundColor: connectorProblem ? 'var(--accent-warning)' : (status.hubRunning ? 'var(--accent-success)' : 'var(--text-muted)')
              }}
            />
            <span>{connectorProblem || hubText}</span>
          </div>
          <button
            type="button"
            data-testid="matrix-refresh-btn"
            onClick={onRefresh}
            disabled={status.loading || catalog.loading}
            style={{ ...btnBase, cursor: status.loading ? 'wait' : 'pointer' }}
          >
            <RefreshCw size={13} />
            <span>Actualizar estado</span>
          </button>
        </div>

        <TerminalNotice
          notice={statusNotice}
          testId="status-notice"
          copiedKey={copiedKey}
          onCopy={onCopy}
        />

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
          <span data-testid="open-count" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            {activePanes.length} de {maxPanes} terminales abiertas
          </span>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '11.5px', color: 'var(--text-secondary)' }}>
            <span>Máximo de paneles</span>
            <select
              data-testid="max-panes-select"
              className="mcp-combobox"
              value={maxPanes}
              onChange={(e) => onSetMaxPanes(parseInt(e.target.value, 10))}
              style={{ height: '27px', fontSize: '11px', width: '64px' }}
            >
              {maxOptions.map((n) => (
                <option key={n} value={n}>{n}{n === 4 ? ' (óptimo)' : ''}</option>
              ))}
            </select>
          </label>
        </div>

        <div>
          <button
            type="button"
            data-testid="toggle-manual-instructions-btn"
            aria-pressed={showManual}
            onClick={() => setShowManual((prev) => !prev)}
            style={btnBase}
          >
            <span>{showManual ? 'Ocultar conexión manual' : 'Conexión manual de un CLI externo'}</span>
          </button>
        </div>
        {showManual && (
          <ManualConnectionCard
            testId="matrix-manual-card"
            manualCommand={status.manualCommand}
            mcpConfig={status.mcpConfig}
            copiedKey={copiedKey}
            onCopy={onCopy}
          />
        )}
      </section>

      {/* Abrir una terminal */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '9px' }}>
        <h4 style={sectionTitle}>Abrir una terminal</h4>
        <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '11.5px', color: 'var(--text-secondary)' }}>
          <span>Carpeta de trabajo</span>
          <input
            type="text"
            data-testid="launch-cwd-input"
            aria-label="Carpeta de trabajo"
            spellCheck={false}
            autoComplete="off"
            value={cwdValue}
            onChange={(e) => onCwdChange(e.target.value)}
            placeholder="Vacío: carpeta del proyecto activo"
            style={fieldStyle}
          />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11.5px', color: 'var(--text-secondary)' }}>
          <span>Rol</span>
          <select
            data-testid="launch-role-select"
            className="mcp-combobox"
            value={roleValue}
            onChange={(e) => onRoleChange(e.target.value)}
            style={{ height: '27px', fontSize: '11px', flex: 1 }}
          >
            {ROLE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>

        {catalog.error && (
          <p data-testid="catalog-error" style={{ ...mutedText, color: 'var(--accent-danger)', margin: 0 }}>{catalog.error}</p>
        )}
        {!catalog.loaded && !catalog.error && <p style={{ ...mutedText, margin: 0 }}>Leyendo el catálogo de CLIs...</p>}
        {catalog.loaded && catalog.clis.length === 0 && !catalog.error && (
          <p data-testid="catalog-empty" style={{ ...mutedText, margin: 0 }}>El catálogo de CLIs está vacío o no está disponible.</p>
        )}
        <ul style={{ margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {catalog.clis.map((cli) => (
            <CliRow
              key={cli.id}
              cli={cli}
              platformSupported={platformSupported}
              launching={launchingCliId === cli.id}
              anyLaunching={Boolean(launchingCliId)}
              onLaunch={onLaunch}
              installPlan={installPlan}
              installBusy={installBusyCliId === cli.id}
              onPrepareInstall={onPrepareInstall}
              onConfirmInstall={onConfirmInstall}
              onCancelInstall={onCancelInstall}
              onSetCliPath={onSetCliPath}
              pathEditRequest={pathEditRequest}
            />
          ))}
        </ul>
        {catalog.warnings?.length > 0 && (
          <ul data-testid="catalog-warnings" style={{ margin: 0, paddingLeft: '16px', ...mutedText }}>
            {catalog.warnings.map((w, i) => (
              <li key={i}>{String(w)}</li>
            ))}
          </ul>
        )}
      </section>

      {/* Terminales activas */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '9px' }}>
        <h4 style={sectionTitle}>Terminales activas</h4>
        {activePanes.length === 0 ? (
          <p data-testid="active-empty" style={{ ...mutedText, margin: 0 }}>No hay terminales abiertas.</p>
        ) : (
          <ul style={{ margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {activePanes.map((pane) => (
              <ActiveTerminalRow
                key={pane.paneId}
                pane={pane}
                cli={catalogById[pane.cliProfile]}
                label={paneLabels[pane.paneId]}
                isFocused={focusedPaneId === pane.paneId}
                onFocusPane={onFocusPane}
                onPausePane={onPausePane}
                onClosePane={onClosePane}
                onResizePane={onResizePane}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
