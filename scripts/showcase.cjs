const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const {spawn, spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const {pythonPath, browserOptions, stopServer} = require('../tests/browser-support.cjs');

const root = path.resolve(__dirname, '..');
const scratch = fs.mkdtempSync(path.join(root, '.notebook-test-showcase-'));
const output = path.join(root, 'docs', 'images');

async function freePort() {
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  return port;
}

async function main() {
  const port = await freePort(), base = `http://127.0.0.1:${port}`;
  const env = {...process.env, LIT_PORT:String(port), LIT_HOST:'127.0.0.1',
    LIT_DB_PATH:path.join(scratch, 'demo.db'), LIT_DATA_DIR:path.join(scratch, 'data'),
    DEEPSEEK_API_KEY:'', LIT_API_KEY:'', LIT_ALLOW_MODEL_DOWNLOAD:'0'};
  const python = pythonPath(root);
  let server, browser, logs = '';
  try {
    const fixture = spawnSync(python, ['-X','utf8','-m','scripts.showcase_fixture'],
      {cwd:root, env, encoding:'utf8', windowsHide:true});
    assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
    const seeded = JSON.parse(fixture.stdout);
    server = spawn(python, ['-X','utf8','-m','scripts.showcase_server'],
      {cwd:root, env, windowsHide:true, stdio:['ignore','pipe','pipe']});
    server.stdout.on('data', data => logs += data);
    server.stderr.on('data', data => logs += data);
    let ready = false;
    for (let attempt = 0; attempt < 150; attempt++) {
      if (server.exitCode !== null) throw new Error(logs);
      try {if ((await fetch(base + '/api/health')).ok) {ready = true; break;}} catch {}
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    assert(ready, logs || 'Demo server did not start');
    fs.mkdirSync(output, {recursive:true});
    browser = await chromium.launch(browserOptions());
    const page = await browser.newPage({viewport:{width:1600,height:1050},deviceScaleFactor:1});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const shot = async name => {
      await page.evaluate(async () => {await document.fonts.ready;});
      if (page.viewportSize().width > 600)
        await page.locator('.main').evaluate(node => node.scrollTo({top:0, behavior:'instant'}));
      await page.screenshot({path:path.join(output, name + '.png')});
    };
    await page.goto(base, {waitUntil:'networkidle'});
    await page.locator('label[for=tab5]').click();
    await page.locator('[data-skill=deep_read]').click();
    await page.locator('#reading-template').selectOption('paper-reading');
    await page.getByLabel('选择阅读论文').selectOption(String(seeded.papers[0]));
    await page.getByRole('tab', {name:'阅读笔记',exact:true}).click();
    await page.locator('#reading-result h1').waitFor();
    await shot('reading-workspace');

    await page.getByRole('tab', {name:'PDF 原文',exact:true}).click();
    const pdf = page.locator('#reading-pdf-preview');
    await page.waitForFunction(() => document.querySelector('#reading-pdf-preview').dataset.state === 'ready');
    await pdf.locator('.textLayer span').filter({hasText:'The policy uses a graph encoder'}).first().evaluate(span => {
      const range = document.createRange(); range.selectNodeContents(span);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      span.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
    });
    await pdf.getByRole('button', {name:'添加批注',exact:true}).click();
    await pdf.getByLabel('个人评论', {exact:true}).fill('我的实验只使用局部观测，需要明确邻居消息是否额外引入通信。先记录通信范围，再设计无通信消融。');
    await pdf.getByLabel('绿色', {exact:true}).check();
    await pdf.getByRole('button', {name:'保存批注',exact:true}).click();
    await pdf.locator('.annotation-highlight').waitFor();
    const ink = await pdf.locator('canvas').evaluate(canvas => {
      const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
      let count = 0;
      for (let i = 0; i < pixels.length; i += 4) if (pixels[i] < 160 && pixels[i+3] > 0) count++;
      return count;
    });
    assert(ink > 1000, 'PDF demo must contain visible content');
    await shot('pdf-annotations');

    await page.locator('label[for=tab6]').click();
    await page.locator(`.notebook-item[data-task-id="${seeded.notes[0]}"]`).click();
    await page.waitForFunction(() => document.querySelector('#notebook-pdf').dataset.state === 'ready');
    await shot('notebook');
    await page.getByRole('button', {name:'个人模板',exact:true}).click();
    await page.locator('#template-editor[aria-busy=false]').waitFor();
    await page.locator('#template-preview h1').waitFor();
    await shot('note-template');
    await page.getByRole('button', {name:'关闭模板管理',exact:true}).click();

    await page.locator('label[for=tab5]').click();
    await page.locator('[data-skill=compare]').click();
    await page.locator('#compare-paper-a').selectOption(String(seeded.papers[0]));
    await page.locator('#compare-paper-b').selectOption(String(seeded.papers[1]));
    await page.getByRole('tab', {name:'阅读笔记',exact:true}).click();
    await page.locator('#reading-result h1').filter({hasText:'通信与记忆'}).waitFor();
    await shot('paper-comparison');

    await page.locator('[data-skill=deep_read]').click();
    await page.getByLabel('选择阅读论文').selectOption(String(seeded.papers[0]));
    await page.locator('#use-personal').check();
    await page.locator('#verify-evidence').uncheck();
    await page.locator('[data-action=question]').click();
    await page.locator('#reading-message').fill('结合我的批注，这个方法如何适配只有局部观测的研究环境？');
    await page.locator('#run-reading').click();
    await page.locator('#run-state').filter({hasText:'已完成'}).waitFor({timeout:30000});
    await page.locator('#reading-result .personal-citation-button').first().waitFor();
    await shot('personal-answer');
    await page.locator('#reading-result .personal-citation-button').first().click();
    await page.locator('.personal-evidence-dialog[open]').waitFor();
    await shot('personal-evidence');
    await page.getByRole('button',{name:'关闭个人材料',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await page.locator('#reading-result').scrollIntoViewIfNeeded();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await shot('reading-mobile');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({screenshots:output,files:8,pdfInk:ink,errors,
      source:'isolated synthetic PDFs and offline fixture answers'}));
  } finally {
    await browser?.close();
    if (server) await stopServer(server);
    const target = fs.realpathSync(scratch), workspace = fs.realpathSync(root);
    if (path.dirname(target).toLowerCase() !== workspace.toLowerCase() ||
        !path.basename(target).startsWith('.notebook-test-showcase-'))
      throw new Error('Refusing cleanup outside showcase workspace');
    fs.rmSync(target, {recursive:true,force:true,maxRetries:8,retryDelay:250});
  }
}
main().catch(error => {console.error(error);process.exitCode = 1;});
