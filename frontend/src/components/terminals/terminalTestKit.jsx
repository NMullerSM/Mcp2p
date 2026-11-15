// Utilidades compartidas por los tests del panel de terminales (no es un test en si).
import React from 'react';
import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { ViewSliderProvider } from '../ViewSlider';
import { SwarmTerminalMatrix } from '../SwarmTerminalMatrix';

export const PROJECT = { project_uuid: 'proj_matrix_1', name: 'Proyecto Matrix' };
export const MANUAL_CMD =
  'mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765';
export const MCP_CONFIG = { mcpServers: { mcp2p: { url: 'http://127.0.0.1:8765/mcp' } } };

export function makeCli(id, over = {}) {
  const shell = id === 'custom_shell';
  return {
    id,
    display_name: shell ? 'Shell' : id.charAt(0).toUpperCase() + id.slice(1),
    icon: shell ? 'shell' : id,
    kind: shell ? 'shell' : 'agent_cli',
    source: 'repo',
    verified: true,
    installed: true,
    resolved_path: `/usr/local/bin/${id}`,
    resolution_source: 'path',
    configured_path: null,
    configured_path_valid: null,
    version: '1.0.0',
    launchable: true,
    resume: { argv: [], verified: false },
    mcp: {},
    install: {
      available: false,
      code: 'OK',
      command: null,
      method: null,
      verified: false,
      downloads_script: false,
      missing_requirements: [],
      source_url: null,
      note: '',
      options: []
    },
    ...over
  };
}

export function notInstalled(id, installOver = {}, over = {}) {
  return makeCli(id, {
    installed: false,
    launchable: false,
    resolved_path: null,
    resolution_source: null,
    version: null,
    install: {
      available: true,
      code: 'OK',
      command: `npm install -g @example/${id}`,
      method: 'npm',
      verified: true,
      downloads_script: false,
      missing_requirements: [],
      source_url: `https://example.com/${id}`,
      note: '',
      options: [],
      ...installOver
    },
    ...over
  });
}

export function makeCatalogResponse(clis, over = {}) {
  return {
    success: true,
    code: 'OK',
    clis,
    max_panes: 4,
    hard_max_panes: 6,
    platform: 'linux',
    terminal_platform_supported: true,
    catalog_version: '2026.11.15',
    warnings: [],
    ...over
  };
}

export const DEFAULT_CLIS = [makeCli('claude', { version: '2.1.0' }), makeCli('gemini'), makeCli('custom_shell')];

export function makeMatrixApi(overrides = {}) {
  return {
    pty_get_matrix_status: vi.fn(async () => ({
      success: true,
      degraded: false,
      connector_status: { available: true, degraded: false, mode: 'posix_pty' },
      hub_url: null,
      hub_running: false,
      panes: [],
      sessions: [],
      manual_command: null,
      mcp_config: null
    })),
    pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse(DEFAULT_CLIS)),
    pty_set_max_panes: vi.fn(async (n) => ({ success: true, code: 'OK', max_panes: n, hard_max_panes: 6 })),
    pty_set_cli_path: vi.fn(async (cliId, path) => ({
      success: true,
      code: 'OK',
      cli_id: cliId,
      configured_path: path || null
    })),
    pty_spawn_pane: vi.fn(async (paneId, cliProfile, role, _cmd, cols, rows, _hub, cwd) => ({
      success: true,
      degraded: false,
      code: 'OK',
      pane_id: paneId,
      node_uuid: `${paneId}_abcd1234`,
      pid: 4242,
      hub_url: 'http://127.0.0.1:51234/mcp',
      cwd: cwd || '/home/u/proyecto',
      pane: { pane_id: paneId, node_uuid: `${paneId}_abcd1234`, cli_profile: cliProfile, role, pid: 4242, status: 'active' },
      session: {
        pane_id: paneId,
        session_id: `${paneId}-sess1`,
        cli_profile: cliProfile,
        role,
        node_uuid: `${paneId}_abcd1234`,
        pid: 4242,
        alive: true,
        paused: false,
        rss_mb: 24.5,
        cols,
        rows
      }
    })),
    pty_poll_output: vi.fn(async (paneId, sinceSeq = 0) => ({
      success: true,
      pane_id: paneId,
      alive: true,
      paused: false,
      rss_mb: 24.5,
      since_seq: sinceSeq,
      next_seq: sinceSeq === 0 ? 1 : sinceSeq,
      truncated: false,
      chunks: [],
      text: sinceSeq === 0 ? 'Agente listo en https://docs.example.com/auth\n' : ''
    })),
    pty_write_input: vi.fn(async (paneId, data) => ({ success: true, pane_id: paneId, bytes_written: data.length })),
    pty_resize_pane: vi.fn(async (paneId, cols, rows) => ({ success: true, pane_id: paneId, cols, rows })),
    pty_pause_pane: vi.fn(async (paneId, paused) => ({ success: true, pane_id: paneId, paused })),
    pty_close_pane: vi.fn(async (paneId) => ({ success: true, pane_id: paneId, closed: true })),
    ...overrides
  };
}

export function renderMatrix(api, props = {}) {
  const tree = (extra) => (
    <ViewSliderProvider viewKey="terminals">
      <SwarmTerminalMatrix api={api} project={PROJECT} {...props} {...extra} />
    </ViewSliderProvider>
  );
  const result = render(tree());
  return { ...result, rerenderMatrix: (extra) => result.rerender(tree(extra)) };
}
