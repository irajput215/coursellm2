#!/usr/bin/env node
/**
 * Regenerate `src/api/schema.d.ts` from the *running* FastAPI application.
 *
 * The schema is never copied by hand: the client types are a build artefact of
 * the live OpenAPI document, so a route or field that changes in the API is a
 * type error here rather than a silently wrong shape at runtime.
 *
 * Usage (from `apps/web`):
 *
 *     npm run schema:generate
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import openapiTS, { astToString } from 'openapi-typescript'

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const apiRoot = resolve(webRoot, '..', 'api')
const python =
  process.env.COURSELLM_PYTHON ?? resolve(webRoot, '..', '..', '.venv', 'bin', 'python')

const EXPORT_CODE =
  'import json;from coursellm.api.app import create_app;print(json.dumps(create_app().openapi()))'

const rawSchema = execFileSync(python, ['-c', EXPORT_CODE], {
  cwd: apiRoot,
  encoding: 'utf8',
  maxBuffer: 32 * 1024 * 1024,
})

const ast = await openapiTS(JSON.parse(rawSchema))
const banner = [
  '/**',
  ' * Generated file — do not edit by hand.',
  ' *',
  ' * Source: the live OpenAPI document of the CourseLLM API.',
  ' * Regenerate with `npm run schema:generate` from apps/web.',
  ' */',
  '',
].join('\n')

const target = resolve(webRoot, 'src', 'api', 'schema.d.ts')
mkdirSync(dirname(target), { recursive: true })
writeFileSync(target, banner + astToString(ast))
process.stdout.write(`Wrote ${target}\n`)
