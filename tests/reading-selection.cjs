const assert = require('node:assert/strict');
const {spawn, spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {pythonPath, browserOptions, stopServer} = require('./browser-support.cjs');

const root = path.resolve(__dirname, '..');
const scratch = fs.mkdtempSync(path.join(root, '.notebook-test-selection-'));
const python = pythonPath(root);
const port = 17870;
const base = `http://127.0.0.1:${port}`;
const output = path.join(root, 'artifacts', 'ui');
const env = {...process.env, LIT_PORT:String(port), LIT_DB_PATH:path.join(scratch, 'test.db'),
  LIT_DATA_DIR:path.join(scratch, 'data'), DEEPSEEK_API_KEY:'', LIT_API_KEY:''};

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

async function main() {
  const fixture = spawnSync(python, ['-X', 'utf8', '-m', 'tests.notebook_fixture'],
    {cwd:root, env, encoding:'utf8', windowsHide:true});
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
  const seeded = JSON.parse(fixture.stdout);
  const server = spawn(python, ['-m', 'lit_agent.webui'],
    {cwd:root, env, windowsHide:true, stdio:'ignore'});
  let browser;
  try {
    let ready = false;
    for (let attempt=0; attempt<100; attempt++) {
      if (server.exitCode !== null) throw new Error('Reading selection server exited');
      try { if ((await fetch(base+'/api/health')).ok) { ready=true; break; } } catch {}
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    assert(ready, 'Reading selection server did not start');
    browser = await chromium.launch(browserOptions());
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [], mutations = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    page.on('request', request => {
      if (request.url().startsWith(base+'/api/') && request.method() !== 'GET')
        mutations.push({url:request.url(),method:request.method()});
    });
    // Retain closed listeners so tests can deliver callbacks already queued before a switch.
    await page.addInitScript(() => {
      window.testStreams = [];
      window.EventSource = class {
        constructor(url) { this.url=url; this.listeners={}; this.closed=false; window.testStreams.push(this); }
        addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
        close() { this.closed=true; }
        emit(name, data) { for (const handler of this.listeners[name] || []) handler({data:JSON.stringify(data)}); }
      };
    });
    await page.goto(base, {waitUntil:'networkidle'});
    await page.locator('label[for=tab5]').click();
    const paper = page.getByLabel('选择阅读论文');
    const result = page.locator('#reading-result');
    const notesTab = page.getByRole('tab', {name:'阅读笔记',exact:true});
    const a = String(seeded.papers[0]), b = String(seeded.papers[1]);
    async function savedNote() {
      await page.waitForFunction(id =>
        document.querySelector('#download-note').getAttribute('href') === `/api/tasks/${id}/notes/download`,
      seeded.notes[0]);
      assert.match(await result.textContent(), /图学习论文阅读/);
    }
    async function emptyNote() {
      await page.waitForFunction(() =>
        document.querySelector('#reading-result').textContent.includes('当前论文暂无阅读笔记') &&
        document.querySelector('#task-progress').textContent === '');
      assert.equal(await page.locator('#run-state').textContent(), '未开始');
      assert.equal(await page.locator('#note-versions option').count(), 0);
      assert.equal(await page.locator('#note-body').inputValue(), '');
      assert.equal(await page.locator('#note-feedback').inputValue(), '');
      assert.equal(await page.locator('#note-approved').isChecked(), false);
      assert.equal(await page.locator('#citation-list').textContent(), '');
      assert.equal(await page.locator('#reading-metrics').textContent(), '');
      assert.equal(await page.locator('#evidence-review').isVisible(), false);
      assert.equal(await page.locator('#note-editor').isVisible(), false);
      assert.equal(await page.locator('#open-saved-note').isVisible(), false);
      assert.equal(await page.locator('#cancel-reading').isDisabled(), true);
      assert.equal(await page.locator('#download-note').getAttribute('href'), null);
      assert.equal(await page.evaluate(() => localStorage.getItem('litagent-last-task')), null);
    }

    await paper.selectOption(a);
    await savedNote();
    assert.equal(await page.getByRole('tab',{name:'PDF 原文',exact:true}).getAttribute('aria-selected'), 'true');
    await notesTab.click();
    await page.locator('#note-editor summary').click();
    await page.locator('#note-body').fill('Unsaved text from paper A');
    await page.locator('#note-feedback').fill('Paper A feedback');
    await page.locator('#note-approved').check();
    await paper.selectOption(b);
    await notesTab.click();
    await emptyNote();
    fs.mkdirSync(output, {recursive:true});
    await page.screenshot({path:path.join(output, 'reading-selection-empty.png')});
    await paper.selectOption(a);
    await savedNote();
    assert(!((await page.locator('#note-body').inputValue()).includes('Unsaved text')));

    // A late saved-note response cannot restore A after selection has moved to B.
    await paper.selectOption(b);
    await emptyNote();
    const notesStarted = deferred(), releaseNotes = deferred(), notesDelivered = deferred();
    const notePath = `**/api/tasks/${seeded.notes[0]}/notes`;
    await page.route(notePath, async route => {
      const response = await route.fetch();
      notesStarted.resolve();
      await releaseNotes.promise;
      await route.fulfill({response});
      notesDelivered.resolve();
    });
    await paper.selectOption(a);
    await notesStarted.promise;
    await paper.selectOption(b);
    await emptyNote();
    releaseNotes.resolve();
    await notesDelivered.promise;
    await page.unroute(notePath);
    await emptyNote();

    // The latest-note lookup itself must also be tied to the selected scope.
    const lookupStarted = deferred(), releaseLookup = deferred(), lookupDelivered = deferred();
    const latestPath = '**/api/notes/latest?*';
    await page.route(latestPath, async route => {
      if (new URL(route.request().url()).searchParams.get('paper_id') !== a) return route.continue();
      const response = await route.fetch();
      lookupStarted.resolve();
      await releaseLookup.promise;
      await route.fulfill({response});
      lookupDelivered.resolve();
    });
    await paper.selectOption(a);
    await lookupStarted.promise;
    await paper.selectOption(b);
    await emptyNote();
    releaseLookup.resolve();
    await lookupDelivered.promise;
    await page.unroute(latestPath);
    await emptyNote();

    // Selecting the same pair in reversed A/B order restores only its comparison note.
    await paper.selectOption(a);
    await savedNote();
    await page.getByRole('button',{name:'论文对比',exact:true}).click();
    await page.getByLabel('论文 A',{exact:true}).selectOption('');
    await page.getByLabel('论文 B',{exact:true}).selectOption(a);
    await page.getByLabel('论文 A',{exact:true}).selectOption(b);
    await page.waitForFunction(id =>
      document.querySelector('#download-note').getAttribute('href') === `/api/tasks/${id}/notes/download`,
    seeded.notes[1]);
    assert.equal(await page.getByLabel('论文 A',{exact:true}).inputValue(), b);
    assert.equal(await page.getByLabel('论文 B',{exact:true}).inputValue(), a);
    await page.getByLabel('论文 B',{exact:true}).selectOption('');
    await emptyNote();
    await page.getByRole('button',{name:'快速概览',exact:true}).click();
    assert.equal(await paper.inputValue(), b);
    await emptyNote();

    // A running task may finish on the server but cannot repaint a different paper.
    const profile = await (await page.request.get(base+'/api/profile')).json();
    let complete = false;
    await page.route('**/api/tasks/switch-running', route => route.fulfill({json:{
      id:'switch-running',kind:'reading',status:complete ? 'succeeded' : 'running',
      stage:complete ? 'done' : 'generating',session_id:999,
      payload:{paper_ids:[seeded.papers[0]],skill_id:'overview',profile},
      result:complete ? {answer:'LATE ANSWER FROM PAPER A',citations:[],usage:{}} : null,
    }}));
    await page.evaluate(() => localStorage.setItem('litagent-last-task','switch-running'));
    await page.reload({waitUntil:'networkidle'});
    await page.waitForFunction(() => window.testStreams.length === 1);
    assert.equal(await paper.inputValue(), a, 'Running task restore must select its paper');
    await page.evaluate(() => window.testStreams[0].emit('delta',{text:'STREAM FROM PAPER A'}));
    assert.match(await result.textContent(), /STREAM FROM PAPER A/);
    await paper.selectOption(b);
    await notesTab.click();
    await emptyNote();
    assert.equal(await page.evaluate(() => window.testStreams[0].closed), true);
    complete = true;
    const terminalResponse = page.waitForResponse(response => response.url().endsWith('/api/tasks/switch-running'));
    await page.evaluate(() => {
      const stream = window.testStreams[0];
      stream.emit('stage',{stage:'generating'});
      stream.emit('progress',{completed:9,total:10});
      stream.emit('delta',{text:'STALE DELTA'});
      stream.emit('done',{});
    });
    await terminalResponse;
    await emptyNote();
    assert.equal(await paper.inputValue(), b);
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output, 'reading-selection-mobile.png'),fullPage:true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+2));
    await paper.selectOption(a);
    await savedNote();
    await notesTab.click();
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:path.join(output, 'reading-selection-restored.png')});
    await page.getByRole('button',{name:'新建阅读对话',exact:true}).click();
    await emptyNote();
    assert.deepEqual(mutations, [], 'Paper switching must not cancel tasks, edit notes, or call the LLM');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({paperSwitch:'passed',savedNotes:'passed',comparison:'passed',
      delayedResponses:'passed',staleEvents:'passed',newSession:'passed',errors,output}));
  } catch (error) {
    console.error('Reading selection check failed:', error);
    throw error;
  } finally {
    await browser?.close();
    await stopServer(server);
    const target = fs.realpathSync(scratch), workspace = fs.realpathSync(root);
    if (path.dirname(target).toLowerCase() !== workspace.toLowerCase() ||
        !path.basename(target).startsWith('.notebook-test-selection-'))
      throw new Error('Refusing cleanup outside the test workspace');
    fs.rmSync(target, {recursive:true,force:true,maxRetries:8,retryDelay:250});
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
