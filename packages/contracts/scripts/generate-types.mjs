import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { compile } from 'json-schema-to-typescript';

const root = new URL('../', import.meta.url);
const schema = JSON.parse(await readFile(new URL('schema/waterguard.schema.json', root), 'utf8'));
const output = await compile(schema, 'WaterGuardContract', {
  bannerComment: '/* Generated from schema/waterguard.schema.json. Run npm run contracts:types. */',
  unreachableDefinitions: true,
});
const target = new URL('generated/index.d.ts', root);
if (process.argv.includes('--check')) {
  if (await readFile(target, 'utf8') !== output) throw new Error('Generated types are stale');
  console.log('Generated TypeScript matches the frozen schema.');
} else {
  await mkdir(new URL('generated/', root), { recursive: true });
  await writeFile(target, output);
}
