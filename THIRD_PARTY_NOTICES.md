# Third-Party Notices

Selected browser assets are bundled so the UI does not require a CDN. Their license texts are preserved alongside the distributed files. This notice does not assign a license to LitAgent's own source code.

| Component | Pinned package | Distributed files | License text |
|---|---|---|---|
| Lucide | `lucide@0.468.0` | `lit_agent/static/vendor/lucide.min.js` | [ISC and included Feather MIT notice](lit_agent/static/vendor/lucide-LICENSE) |
| Marked | `marked@18.2.0` | `lit_agent/static/vendor/marked.umd.js` | [MIT](lit_agent/static/vendor/marked-LICENSE) |
| DOMPurify | `dompurify@3.4.17` | `lit_agent/static/vendor/purify.min.js` | [Bundled Apache 2.0 license](lit_agent/static/vendor/dompurify-LICENSE) |
| PDF.js | `pdfjs-dist@5.4.624` | `lit_agent/static/vendor/pdfjs/` | [Apache 2.0](lit_agent/static/vendor/pdfjs/LICENSE) |

PDF.js includes supporting fonts, character maps and WebAssembly resources with retained license files where supplied. Review the complete vendor tree before redistributing individual resources.

Run `npm ci` and `npm run vendor` to reproduce assets from the lockfile. Copying rules are in [scripts/vendor.cjs](scripts/vendor.cjs). Preserve license files during upgrades.

Python dependencies and Playwright are installed through package managers rather than copied here. Consult their distributions for license conditions. E5 model weights are downloaded separately and are not included in this repository.

The showcase PDF source text is an original synthetic fixture. Screenshots show it running in the application; they do not reproduce private libraries or published papers.
