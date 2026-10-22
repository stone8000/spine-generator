#!/usr/bin/env node
// Entry point. Node 22.18+ runs the TypeScript sources directly.
import { main } from '../src/cli.ts';
process.exitCode = main(process.argv.slice(2));
