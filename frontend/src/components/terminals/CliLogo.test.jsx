// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import React from 'react';
import { CliLogo, CLI_ICON_IDS, resolveCliIconId } from './CliLogo';

afterEach(cleanup);

describe('CliLogo', () => {
  it.each(CLI_ICON_IDS)('dibuja un SVG inline para %s sin imagenes externas', (id) => {
    const { container } = render(<CliLogo cliId={id} icon={id} name={id} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeTruthy();
    expect(svg.getAttribute('data-icon')).toBe(id);
    expect(container.querySelector('img, image, use, foreignObject')).toBeNull();
  });

  it('custom_shell cae al icono shell y un id desconocido al monograma', () => {
    expect(resolveCliIconId('custom_shell', undefined)).toBe('shell');
    expect(resolveCliIconId('claude', 'claude')).toBe('claude');
    expect(resolveCliIconId('agy', undefined)).toBe('antigravity');
    expect(resolveCliIconId('mi_cli', 'otra')).toBe('generic');
    const { container } = render(<CliLogo cliId="mi_cli" name="Mi CLI" />);
    expect(container.querySelector('svg').getAttribute('data-icon')).toBe('generic');
    expect(container.querySelector('text').textContent).toBe('M');
  });
});
