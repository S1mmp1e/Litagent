// Read-only smoke check: selected papers must already have local PDF files.
const {chromium} = require('playwright');
const path = require('node:path');
const {browserOptions} = require('./browser-support.cjs');
const base = process.env.LIT_TEST_URL || 'http://127.0.0.1:7860';
const ids = process.argv.length > 2 ? process.argv.slice(2).map(Number) : [1,35];

async function main() {
  for (const id of ids) {
    const response = await fetch(`${base}/api/papers/${id}/preview`);
    const info = await response.json();
    if (!response.ok || !info.pdf_url) throw new Error(`Paper ${id} has no local PDF; refusing a download.`);
  }
  const browser = await chromium.launch(browserOptions());
  const findings = [], errors = [];
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1080}});
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', route => {
      if (!['GET','HEAD'].includes(route.request().method())) {
        errors.push('Unexpected write request'); return route.abort();
      }
      return route.continue();
    });
    await page.goto(base, {waitUntil:'networkidle'});
    const output = path.resolve(__dirname, '..', 'artifacts', 'ui');
    for (const id of ids) {
      await page.getByLabel('选择阅读论文').selectOption(String(id));
      const preview = page.locator(`#reading-pdf-preview[data-paper-id="${id}"][data-state="ready"]`);
      await preview.locator('canvas').waitFor();
      const ink = await preview.locator('canvas').evaluate(canvas => {
        const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
        let n = 0;
        for (let i=0;i<pixels.length;i+=4) if (pixels[i]<160 && pixels[i+1]<160 && pixels[i+2]<160) n++;
        return n;
      });
      if (ink < 1000) throw new Error(`Blank real-paper PDF ${id}`);
      findings.push({paper_id:id,title:await preview.locator('.pdf-title').textContent(),
        pages:await preview.locator('.pdf-page-count').textContent(),inkPixels:ink});
      await page.locator('.main').evaluate(node => { node.scrollTop=0; });
      await page.screenshot({path:path.join(output,`pdf-live-${id}.png`)});
    }
    await page.setViewportSize({width:390,height:844});
    const preview = page.locator('#reading-pdf-preview');
    await page.waitForFunction(() => {
      const root = document.querySelector('#reading-pdf-preview');
      return root.querySelector('canvas').getBoundingClientRect().width <= root.querySelector('.pdf-viewport').clientWidth;
    });
    await preview.locator('.textLayer span').first().waitFor({state:'attached'});
    await preview.screenshot({path:path.join(output,'pdf-live-mobile.png')});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2))
      throw new Error('Live PDF overflows on mobile');
    if (errors.length) throw new Error(errors.join('; '));
    console.log(JSON.stringify({findings,errors,output}));
  } finally { await browser.close(); }
}
main().catch(error => {console.error(error);process.exitCode=1;});
