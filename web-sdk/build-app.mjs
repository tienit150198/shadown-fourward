import { build, transform } from 'esbuild';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const web = fileURLToPath(new URL('../web/', import.meta.url));
const output = path.join(web, '.build');
await mkdir(output, { recursive: true });
const app = await build({
  entryPoints: [path.join(web, 'app.js')], outfile: path.join(output, 'app.js'),
  bundle: true, minify: true, format: 'esm', target: 'es2020', metafile: true, write: false,
  plugins: [{ name: 'optional-camera-sdks', setup(builder) {
    builder.onResolve({ filter: /\/vendor\// }, args => ({ path: '/vendor/' + args.path.split('/vendor/')[1], external: true }));
  } }],
});
const cssNames = ['styles.css', 'wardrobe.css', 'tryon-live.css', 'conversation.css'];
const css = await transform((await Promise.all(cssNames.map(name => readFile(path.join(web, name), 'utf8')))).join('\n'), { loader: 'css', minify: true, target: 'es2020' });
const manifest = {};
for (const [name, inputs] of Object.entries({
  'app.js': Object.keys(app.metafile.inputs).map(input => path.resolve(input)),
  'styles.css': cssNames.map(name => path.join(web, name)),
})) {
  manifest[name] = Object.fromEntries(await Promise.all(inputs.map(async input => [path.relative(web, input), createHash('sha256').update(await readFile(input)).digest('hex')])));
}
await writeFile(path.join(output, 'app.js'), app.outputFiles[0].contents);
await writeFile(path.join(output, 'styles.css'), css.code);
await writeFile(path.join(output, 'manifest.json'), JSON.stringify(manifest));
console.log(`SKI frontend: 1 script (${app.outputFiles[0].contents.length} bytes), 1 stylesheet (${Buffer.byteLength(css.code)} bytes).`);
