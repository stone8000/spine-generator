/**
 * spine-core 4.3.13, re-exported from THIS package.
 *
 * WHY: Node resolves a bare "@esotericsoftware/spine-core" from the importing
 * file's directory, so qa/ (which pins 4.2.120) cannot name 4.3.13 directly.
 * qa/lib/rt43.mjs imports this file instead; the import below resolves inside
 * qa43/ and binds to the 4.3.13 copy installed here (npm ci in qa43/).
 */
export * from "@esotericsoftware/spine-core";
