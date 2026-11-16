const fs = require('node:fs');
const path = require('node:path');
const target = path.join(__dirname, '..', 'lit_agent', 'static', 'vendor');
fs.mkdirSync(target, {recursive: true});
fs.copyFileSync(require.resolve('lucide/dist/umd/lucide.min.js'), path.join(target, 'lucide.min.js'));
fs.copyFileSync(path.join(path.dirname(require.resolve('lucide/package.json')), 'LICENSE'), path.join(target, 'lucide-LICENSE'));
for (const [pkg, source, name] of [
  ['marked', 'lib/marked.umd.js', 'marked.umd.js'],
  ['dompurify', 'dist/purify.min.js', 'purify.min.js'],
]) {
  const directory = path.join(__dirname, '..', 'node_modules', pkg);
  fs.copyFileSync(path.join(directory, source), path.join(target, name));
  const license = fs.readdirSync(directory).find(file => /^license(?:\.|$)/i.test(file));
  if (!license) throw new Error(`Missing license for ${pkg}`);
  fs.copyFileSync(path.join(directory, license), path.join(target, `${pkg}-LICENSE`));
}
const pdfjs = path.join(__dirname, '..', 'node_modules', 'pdfjs-dist');
const pdfTarget = path.join(target, 'pdfjs');
fs.mkdirSync(pdfTarget, {recursive:true});
for (const name of ['pdf.min.mjs', 'pdf.worker.min.mjs']) {
  fs.copyFileSync(path.join(pdfjs, 'build', name), path.join(pdfTarget, name));
}
for (const name of ['cmaps', 'standard_fonts', 'wasm']) {
  fs.cpSync(path.join(pdfjs, name), path.join(pdfTarget, name), {recursive:true});
}
fs.copyFileSync(path.join(pdfjs, 'web', 'pdf_viewer.css'), path.join(pdfTarget, 'pdf_viewer.css'));
fs.copyFileSync(path.join(pdfjs, 'LICENSE'), path.join(pdfTarget, 'LICENSE'));
