// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import React from 'react';
import BrandMark from './BrandMark';

describe('BrandMark', () => {
  it('renderiza el SVG y el aria-label="MCP²P"', () => {
    const { container, getByLabelText } = render(<BrandMark size={28} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeTruthy();
    expect(svg.getAttribute('width')).toBe('28');
    const wordmark = getByLabelText('MCP²P');
    expect(wordmark).toBeTruthy();
  });
});
