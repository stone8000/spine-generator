/** Node-only file I/O, kept out of the engine so the engine also runs in the browser (motion studio). */
import { readFileSync, writeFileSync } from 'node:fs';
import type { SpineJson } from './spineJson.ts';

export function loadJson(path: string): SpineJson {
  return JSON.parse(readFileSync(path, 'utf8'));
}

export function saveJson(path: string, json: SpineJson): void {
  writeFileSync(path, JSON.stringify(json, null, 1), 'utf8');
}
