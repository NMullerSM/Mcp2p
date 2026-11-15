// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const css = readFileSync(fileURLToPath(new URL('./theme.css', import.meta.url)), 'utf8');

function cssVarMs(name) {
  const m = css.match(new RegExp(`${name}:\\s*(\\d+)ms`));
  return m ? Number(m[1]) : null;
}

describe('animacion de la barra superior (theme.css)', () => {
  it('define duracion lenta (380-450 ms) y retardo de intencion (120-160 ms)', () => {
    const duration = cssVarMs('--mcp-nav-duration');
    const delay = cssVarMs('--mcp-nav-delay');
    expect(duration).toBeGreaterThanOrEqual(380);
    expect(duration).toBeLessThanOrEqual(450);
    expect(delay).toBeGreaterThanOrEqual(120);
    expect(delay).toBeLessThanOrEqual(160);
  });

  it('el estado base (cierre) y el hover (apertura) no sobreescriben duracion ni retardo, asi que ambos sentidos son iguales', () => {
    const rules = [...css.matchAll(/(\.mcp-nav-icon-tab[^{]*)\{([^}]*)\}/g)];
    const withHoverState = rules.filter(([, sel]) => /:hover|\.is-active/.test(sel));
    expect(withHoverState.length).toBeGreaterThan(0);
    for (const [, , body] of withHoverState) {
      expect(body).not.toMatch(/transition/);
    }
  });

  it('respeta prefers-reduced-motion', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*mcp-nav-icon-tab[\s\S]*transition-delay:\s*0s/);
  });
});
