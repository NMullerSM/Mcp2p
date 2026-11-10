import React from 'react';

export default function BrandMark({ size = 28, showWordmark = true }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', userSelect: 'none' }}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ flexShrink: 0, display: 'block' }}
        aria-hidden="true"
      >
        <path
          d="M 36 26 L 18 50 L 36 74"
          stroke="var(--brand-emerald)"
          strokeWidth="7.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M 64 26 L 82 50 L 64 74"
          stroke="var(--brand-emerald)"
          strokeWidth="7.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="41" cy="50" r="5.5" fill="var(--brand-emerald)" />
        <rect x="46" y="47.5" width="8" height="5" rx="2.5" fill="var(--text-primary)" />
        <circle cx="59" cy="50" r="5.5" fill="var(--brand-emerald)" />
      </svg>
      {showWordmark && (
        <span
          aria-label="MCP²P"
          style={{
            fontWeight: 800,
            fontSize: '16px',
            letterSpacing: '-0.5px',
            fontFamily: 'system-ui, -apple-system, Inter, "Segoe UI", Roboto, sans-serif',
            color: 'var(--text-primary)',
            display: 'inline-flex',
            alignItems: 'center',
            lineHeight: 1
          }}
        >
          MCP<sup aria-hidden="true" style={{ fontSize: '64%', color: 'var(--brand-emerald)', verticalAlign: 'super', lineHeight: 0 }}>2</sup>P
        </span>
      )}
    </div>
  );
}
