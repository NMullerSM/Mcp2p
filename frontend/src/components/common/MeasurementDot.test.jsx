// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import { MeasurementDot } from './MeasurementDot';

afterEach(() => {
  cleanup();
});

describe('MeasurementDot', () => {
  it('maps_each_color_to_its_theme_token', () => {
    const cases = [
      ['green', 'var(--accent-success)'],
      ['yellow', 'var(--accent-warning)'],
      ['red', 'var(--accent-danger)'],
      ['gray', 'var(--text-muted)']
    ];
    for (const [color, token] of cases) {
      const { unmount } = render(<MeasurementDot color={color} label={`Estado ${color}`} />);
      const dot = screen.getByRole('img', { name: `Estado ${color}` });
      expect(dot.getAttribute('data-dot')).toBe(color);
      expect(dot.style.backgroundColor).toBe(token);
      unmount();
    }
  });

  it('exposes_label_as_accessible_name_and_tooltip', () => {
    render(<MeasurementDot color="red" label="No se pudo medir" />);
    const dot = screen.getByRole('img', { name: 'No se pudo medir' });
    expect(dot.getAttribute('aria-label')).toBe('No se pudo medir');
    expect(dot.getAttribute('title')).toBe('No se pudo medir');
  });

  it('unknown_color_falls_back_to_gray', () => {
    render(<MeasurementDot color="purple" label="Desconocido" />);
    const dot = screen.getByRole('img', { name: 'Desconocido' });
    expect(dot.getAttribute('data-dot')).toBe('gray');
    expect(dot.style.backgroundColor).toBe('var(--text-muted)');
  });

  it('missing_color_falls_back_to_gray', () => {
    render(<MeasurementDot label="Sin color" />);
    expect(screen.getByRole('img', { name: 'Sin color' }).getAttribute('data-dot')).toBe('gray');
  });

  it('size_defaults_to_ten_pixels_and_accepts_override', () => {
    const { unmount } = render(<MeasurementDot color="green" label="Por defecto" />);
    const def = screen.getByRole('img', { name: 'Por defecto' });
    expect(def.style.width).toBe('10px');
    expect(def.style.height).toBe('10px');
    unmount();

    render(<MeasurementDot color="green" label="Grande" size={16} />);
    const big = screen.getByRole('img', { name: 'Grande' });
    expect(big.style.width).toBe('16px');
    expect(big.style.height).toBe('16px');
  });
});
