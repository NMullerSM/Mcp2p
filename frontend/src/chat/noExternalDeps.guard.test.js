import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Guarda de aislamiento: el modulo de chat es autonomo. No importa nada que salga de su carpeta
// (ni las areas de terminales, relevo o ajustes) y solo usa paquetes externos de una lista cerrada.
// Patron de prueba-guardia como noLegacyOutput.guard.test.js: lee el codigo fuente, no lo ejecuta.

const CHAT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FORBIDDEN_AREAS = ['components/terminals', 'components/relay', 'components/settings'];
const ALLOWED_PACKAGES = new Set([
  'react',
  'lucide-react',
  'vitest',
  '@testing-library/react',
  'node:fs',
  'node:path',
  'node:url'
]);

const SPECIFIER_PATTERNS = [
  // Importaciones con "from" (default, nombradas, type), de efecto secundario, dinamicas y require.
  /\bimport\s+(?:type\s+)?[^'";]*?\bfrom\s*['"]([^'"]+)['"]/g,
  /\bimport\s*['"]([^'"]+)['"]/g,
  /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
  /\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
  // Reexportaciones "export ... from".
  /\bexport\s+[^'";]*?\bfrom\s*['"]([^'"]+)['"]/g
];

function listSources(dir) {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(jsx?|tsx?)$/.test(entry.name))
    .map((entry) => path.join(dir, entry.name));
}

function specifiersOf(file) {
  // Las lineas de comentario no cuentan: solo el codigo produce importaciones.
  const text = fs.readFileSync(file, 'utf8').replace(/^\s*\/\/.*$/gm, '');
  const found = [];
  for (const pattern of SPECIFIER_PATTERNS) {
    for (const match of text.matchAll(pattern)) found.push(match[1]);
  }
  return found;
}

function violationsOf(file) {
  const problems = [];
  for (const spec of specifiersOf(file)) {
    if (FORBIDDEN_AREAS.some((area) => spec.includes(area))) {
      problems.push(`${spec} (area prohibida)`);
      continue;
    }
    if (spec.startsWith('.')) {
      const target = path.resolve(path.dirname(file), spec);
      const rel = path.relative(CHAT_DIR, target);
      if (rel.startsWith('..') || path.isAbsolute(rel)) problems.push(`${spec} (sale de chat/)`);
      continue;
    }
    if (!ALLOWED_PACKAGES.has(spec)) problems.push(`${spec} (paquete no permitido)`);
  }
  return problems;
}

const SOURCES = listSources(CHAT_DIR);

describe('el modulo de chat es autonomo', () => {
  it('encuentra los archivos del modulo (la guarda no es vacia)', () => {
    const names = SOURCES.map((file) => path.basename(file));
    expect(names).toEqual(expect.arrayContaining(['chatModel.js', 'ChatDock.jsx', 'useChatFeed.js', 'index.js']));
  });

  it.each(SOURCES.map((file) => [path.basename(file), file]))(
    '%s no importa nada fuera de chat/ ni de las areas prohibidas',
    (_name, file) => {
      expect(violationsOf(file)).toEqual([]);
    }
  );

  it('ningun archivo del modulo menciona terminals, relay o settings en un import', () => {
    for (const file of SOURCES) {
      for (const spec of specifiersOf(file)) {
        for (const area of FORBIDDEN_AREAS) expect(spec.includes(area), `${path.basename(file)} importa ${spec}`).toBe(false);
      }
    }
  });
});
