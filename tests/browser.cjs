const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const {pythonPath, browserOptions, stopServer, mockModelSettings} = require('./browser-support.cjs');

const root = path.resolve(__dirname, '..');
const scratch = fs.mkdtempSync(path.join(root, '.browser-test-'));
const screenshots = path.join(root, 'artifacts', 'ui');
fs.mkdirSync(screenshots, {recursive:true});
const python = pythonPath(root);
const port = 17867;

async function main() {
  const server = spawn(python, ['-m', 'lit_agent.webui'], {
    cwd: root, windowsHide: true, stdio: 'ignore',
    env: {...process.env, LIT_PORT: String(port), LIT_DB_PATH: path.join(scratch, 'app.db'),
      LIT_DATA_DIR: path.join(scratch, 'data'), DEEPSEEK_API_KEY: '', LIT_API_KEY: ''},
  });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 80; i++) {
      if (server.exitCode !== null) throw new Error(`Test server exited with ${server.exitCode}`);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/api/health`);
        if (response.ok) { ready = true; break; }
      } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (!ready) throw new Error('Test server did not become ready.');
    browser = await chromium.launch(browserOptions());
    const page = await browser.newPage({viewport:{width:1440,height:1000}, deviceScaleFactor:1});
    await mockModelSettings(page);
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route('**/api/library/rows', route => route.fulfill({json:{
      rows:[
        [1,'Attention Is All You Need',2017,'Vaswani et al.','Transformer','NLP'],
        [2,'Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks',2020,'Lewis et al.','RAG','NLP'],
        [3,'ReAct: Synergizing Reasoning and Acting in Language Models',2022,'Yao et al.','Agents','AI'],
      ], stats_md:'**论文：** 3  \n**标签：** `Transformer`、`RAG`、`Agents`',
      cat_md:'| ID | 分类夹 | 论文数 |\n|---|---|---|\n| 1 | Natural Language Processing | 3 |',
    }}));
    await page.route(/\/api\/papers\/[123]\/documents$/, route => route.fulfill({
      json:route.request().url().endsWith('/3/documents') ? [] : [{status:'ready'}],
    }));
    const fixture = spawnSync(python, ['-c', [
      'import io, sys',
      'from reportlab.pdfgen import canvas',
      'buffer = io.BytesIO()',
      'pdf = canvas.Canvas(buffer, invariant=1)',
      "pdf.setFont('Helvetica-Bold', 22)",
      "pdf.drawString(60, 760, 'PDF PREVIEW TEST')",
      "pdf.setFont('Helvetica', 13)",
      "pdf.drawString(60, 720, 'A real PDF document rendered inside the reading workspace.')",
      "pdf.drawString(60, 690, 'This fixture checks preview only, not scientific evidence.')",
      'pdf.showPage()',
      "pdf.drawString(60, 760, 'SECOND PAGE')",
      'pdf.save()',
      'sys.stdout.buffer.write(buffer.getvalue())',
    ].join('\n')], {cwd:root,windowsHide:true});
    if (fixture.status !== 0) throw new Error('Could not generate PDF fixture');
    let releaseDownload, downloadStarted, secondCached = false, previewPosts = 0;
    const startedDownload = new Promise(resolve => { downloadStarted = resolve; });
    const delayedDownload = new Promise(resolve => { releaseDownload = resolve; });
    await page.route(/\/api\/papers\/[123]\/preview$/, async route => {
      const id = Number(route.request().url().match(/papers\/(\d+)/)[1]);
      const info = {paper_id:id,title:['','Attention Is All You Need','Retrieval-Augmented Generation','ReAct'][id],
        pdf_url:id === 1 || (id === 2 && secondCached) ? `/api/documents/${id}/pdf` : null,
        page_count:2,source_url:id === 3 ? '' : 'https://arxiv.org/pdf/1706.03762',can_download:id !== 3};
      if (route.request().method() === 'POST') {
        previewPosts++;
        downloadStarted();
        await delayedDownload;
        secondCached = true;
        info.pdf_url = `/api/documents/${id}/pdf`;
      }
      await route.fulfill({json:info});
    });
    await page.route(/\/api\/documents\/[12]\/pdf$/, route => route.fulfill({
      contentType:'application/pdf',headers:{'Content-Disposition':'inline'},body:fixture.stdout,
    }));
    await page.route(/\/api\/library\/detail\/[123]$/, route => route.fulfill({
      json:{md:'## Fixture metadata',arxiv_id:'1706.03762',pdf_url:'https://arxiv.org/pdf/1706.03762',bibtex:''},
    }));
    await page.route('**/api/categories', route => route.fulfill({json:[{id:1,name:'NLP',approved_count:1}]}));
    await page.route('**/api/categories/1/papers', route => route.fulfill({json:{
      rows:[[1,'Attention Is All You Need',2017,'Vaswani et al.','Transformer','1706.03762']],info:'NLP',
    }}));
    async function assertPDF(selector, paperId, pageNumber = 1) {
      const surface = page.locator(`${selector}[data-paper-id="${paperId}"][data-state="ready"][data-page="${pageNumber}"]`);
      await surface.locator('canvas').waitFor();
      const pixels = await surface.locator('canvas').evaluate(canvas => {
        const data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
        let ink = 0, white = 0;
        for (let i=0;i<data.length;i+=4) {
          if (data[i]<180 && data[i+1]<180 && data[i+2]<180 && data[i+3]>0) ink++;
          if (data[i]>240 && data[i+1]>240 && data[i+2]>240 && data[i+3]>0) white++;
        }
        return {ink,white};
      });
      if (pixels.ink < 100 || pixels.white < 1000) throw new Error(`Blank PDF canvas: ${JSON.stringify(pixels)}`);
      await surface.locator('.textLayer').filter({hasText:pageNumber === 1 ? 'PDF PREVIEW TEST' : 'SECOND PAGE'}).waitFor();
    }
    await page.goto(`http://127.0.0.1:${port}`, {waitUntil:'networkidle'});
    const categoryManager = page.locator('.sidebar .accordion');
    const categoryMutations = [];
    await page.route('**/api/categories/1', route => {
      categoryMutations.push({method:route.request().method(),body:route.request().postDataJSON()});
      return route.fulfill({json:{ok:true,msg:'Category fixture updated'}});
    });
    for (const viewport of [
      {width:1440,height:1000},
      {width:1366,height:768},
      {width:1024,height:600},
      {width:390,height:844},
      {width:390,height:600},
    ]) {
      await page.setViewportSize(viewport);
      if (viewport.width <= 900)
        await page.getByRole('button',{name:'打开文献管理',exact:true}).click();
      await page.locator('.library-overview summary').click();
      await page.locator('.sidebar-tools summary').click();
      await categoryManager.locator('summary').click();
      await page.locator('#mgmt-cat-dd').selectOption('1');
      await page.locator('#rename-cat-input').fill('Layout test category');
      await categoryManager.getByRole('button',{name:'重命名',exact:true}).click({trial:true});
      await categoryManager.getByRole('button',{name:'删除',exact:true}).click({trial:true});
      const layout = await categoryManager.evaluate(manager => {
        const sidebar = manager.closest('.sidebar');
        return {
          clipped:manager.scrollHeight > manager.clientHeight + 1,
          horizontalOverflow:sidebar.scrollWidth > sidebar.clientWidth + 1,
          scrollable:sidebar.scrollHeight > sidebar.clientHeight,
        };
      });
      if (layout.clipped || layout.horizontalOverflow || !layout.scrollable)
        throw new Error(`Category controls are clipped: ${JSON.stringify({viewport,layout})}`);
      await page.screenshot({path:path.join(screenshots,`sidebar-${viewport.width}x${viewport.height}.png`)});
      if (viewport.width === 390 && viewport.height === 600) {
        await categoryManager.getByRole('button',{name:'重命名',exact:true}).click();
        await page.locator('#manage-cat-msg').filter({hasText:'Category fixture updated'}).waitFor();
        await page.waitForFunction(() => document.querySelector('#rename-cat-input').value === '');
        await page.locator('#mgmt-cat-dd').selectOption('1');
        page.once('dialog', dialog => dialog.accept());
        await Promise.all([
          page.waitForResponse(response => response.request().method() === 'DELETE' &&
            response.url().endsWith('/api/categories/1')),
          categoryManager.getByRole('button',{name:'删除',exact:true}).click(),
        ]);
      }
      await categoryManager.locator('summary').click();
      await page.locator('.sidebar-tools summary').click();
      await page.locator('.library-overview summary').click();
      await page.locator('.sidebar').evaluate(sidebar => { sidebar.scrollTop=0; });
      if (viewport.width <= 900) await page.keyboard.press('Escape');
    }
    if (categoryMutations.length !== 2 || categoryMutations[0].method !== 'PUT' ||
        categoryMutations[0].body.name !== 'Layout test category' ||
        categoryMutations[1].method !== 'DELETE')
      throw new Error(`Category actions failed: ${JSON.stringify(categoryMutations)}`);
    await page.setViewportSize({width:1440,height:1000});
    await page.getByText('论文阅读', {exact:true}).click();
    await page.locator('.empty-state img').waitFor();
    if (!(await page.locator('.empty-state img').evaluate(img => img.complete && img.naturalWidth > 0)))
      throw new Error('Reading artwork did not load');
    await page.screenshot({path:path.join(screenshots,'workspace-empty.png')});
    await page.locator('#reading-papers option').first().waitFor({state:'attached'});
    await page.getByLabel('筛选论文标题').fill('retrieval');
    const visibleOptions = await page.locator('#reading-papers').evaluate(select => [...select.options].filter(o => !o.hidden).length);
    if (visibleOptions !== 1) throw new Error('Paper filter failed');
    await page.getByLabel('筛选论文标题').fill('');
    await page.getByLabel('选择阅读论文').selectOption('1');
    await page.locator('#selected-paper-title').filter({hasText:'Attention Is All You Need'}).waitFor();
    const preview = page.locator('#reading-pdf-preview');
    await assertPDF('#reading-pdf-preview', 1);
    if (await page.getByRole('tab',{name:'PDF 原文',exact:true}).getAttribute('aria-selected') !== 'true' ||
        await page.locator('#reading-notes-panel').isVisible())
      throw new Error('Selecting a paper did not open its inline PDF');
    await preview.screenshot({path:path.join(screenshots,'pdf-desktop.png')});
    await preview.getByRole('button',{name:'下一页',exact:true}).click();
    await assertPDF('#reading-pdf-preview', 1, 2);
    if (!(await preview.getByRole('button',{name:'下一页',exact:true}).isDisabled()))
      throw new Error('PDF page navigation exceeded the last page');
    await preview.getByRole('spinbutton',{name:'PDF 页码',exact:true}).fill('1');
    await preview.getByRole('spinbutton',{name:'PDF 页码',exact:true}).press('Enter');
    await assertPDF('#reading-pdf-preview', 1);
    const initialWidth = await preview.locator('canvas').evaluate(canvas => canvas.width);
    await preview.getByRole('button',{name:'放大',exact:true}).click();
    await page.waitForFunction(width => document.querySelector('#reading-pdf-preview canvas').width > width, initialWidth);
    await preview.getByRole('button',{name:'适应宽度',exact:true}).click();
    await page.waitForFunction(width => document.querySelector('#reading-pdf-preview canvas').width === width, initialWidth);
    await assertPDF('#reading-pdf-preview', 1);
    await page.getByLabel('选择阅读论文').selectOption('2');
    await startedDownload;
    await preview.getByRole('status').filter({hasText:'正在下载'}).waitFor();
    await page.getByLabel('选择阅读论文').selectOption('3');
    await preview.getByRole('status').filter({hasText:'暂无 PDF 文件'}).waitFor();
    releaseDownload();
    await page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/2/preview'));
    if (await preview.locator('.pdf-reader').isVisible() || !(await preview.locator('.pdf-title').textContent()).includes('ReAct'))
      throw new Error('An old PDF download replaced the current selection');
    await page.getByLabel('选择阅读论文').selectOption('2');
    await assertPDF('#reading-pdf-preview', 2);
    if (previewPosts !== 1) throw new Error('Cached PDF was downloaded again');
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(() => {
      const root = document.querySelector('#reading-pdf-preview');
      return root.querySelector('canvas').getBoundingClientRect().width <= root.querySelector('.pdf-viewport').clientWidth;
    });
    await assertPDF('#reading-pdf-preview', 2);
    await preview.screenshot({path:path.join(screenshots,'pdf-mobile.png')});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2))
      throw new Error('PDF preview overflows on mobile');
    await page.setViewportSize({width:1440,height:1000});
    await page.getByText('我的文献库',{exact:true}).click();
    await page.locator('#lib-table-body tbody tr').first().click();
    await assertPDF('#lib-pdf-preview', 1);
    await page.locator('#lib-pdf-preview').screenshot({path:path.join(screenshots,'pdf-library.png')});
    await page.getByText('分类浏览',{exact:true}).click();
    await page.locator('#browse-cat-dd').selectOption('1');
    await page.locator('#cat-table-body tbody tr').first().click();
    await assertPDF('#cat-pdf-preview', 1);
    await page.getByText('论文阅读',{exact:true}).click();
    await page.getByLabel('选择阅读论文').selectOption('1');
    await page.getByRole('button',{name:'论文对比',exact:true}).click();
    if (await page.getByLabel('论文 A',{exact:true}).inputValue() !== '1' ||
        await page.getByLabel('论文 B',{exact:true}).inputValue() !== '' ||
        await page.locator('#single-paper-selection').isVisible())
      throw new Error('Comparison did not expose two separate selectors');
    await page.getByLabel('论文 B',{exact:true}).selectOption('2');
    await assertPDF('#reading-pdf-preview', 2);
    await page.getByRole('group',{name:'预览论文',exact:true}).getByRole('button',{name:'论文 A',exact:true}).click();
    await assertPDF('#reading-pdf-preview', 1);
    if (!(await page.locator('#compare-paper-b option[value="1"]').isDisabled()))
      throw new Error('Comparison allowed duplicate paper selection');
    await page.getByLabel('论文 A',{exact:true}).selectOption('3');
    await page.locator('#compare-selection-status').filter({hasText:'1 篇待处理全文'}).waitFor();
    await page.getByLabel('论文 A',{exact:true}).selectOption('1');
    await page.locator('#compare-selection-status').filter({hasText:'已选 2 / 2 篇'}).waitFor();
    await page.locator('.reading-input').screenshot({path:path.join(screenshots,'compare-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.locator('.reading-input').screenshot({path:path.join(screenshots,'compare-mobile.png')});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2))
      throw new Error('Comparison selectors overflow on mobile');
    await page.setViewportSize({width:1440,height:1000});
    await page.getByRole('button',{name:'复现指南',exact:true}).click();
    if (!(await page.locator('#single-paper-selection').isVisible()) ||
        (await page.locator('#reading-papers').evaluate(node => node.selectedOptions.length)) !== 1)
      throw new Error('Leaving comparison did not restore a single selected paper');
    if (!(await page.locator('#reading-message').inputValue()).includes('复现清单'))
      throw new Error('Skill default request was not applied');
    await page.locator('#reading-message').fill('Keep my custom research question');
    await page.getByRole('button',{name:'方法精读',exact:true}).click();
    if (await page.locator('#reading-message').inputValue() !== 'Keep my custom research question' ||
        await page.locator('#reading-papers').inputValue() !== '1')
      throw new Error('Skill switch discarded the custom request or paper selection');
    await page.locator('.profile-section summary').click();
    await page.getByLabel('研究档案模板').selectOption('uav_rl');
    if (!(await page.getByLabel('研究方向',{exact:true}).inputValue()).includes('无人机'))
      throw new Error('Profile preset did not fill the form');
    const unsavedProfile = await page.request.get(`http://127.0.0.1:${port}/api/profile`);
    if ((await unsavedProfile.json()).research_domain) throw new Error('Preset was saved without pressing Save');
    await page.getByLabel('知识背景').fill('Computer science graduate student');
    await page.getByLabel('关注内容').fill('method design and evaluation');
    await page.getByLabel('可用算力').fill('Single GPU, 12 GB');
    await page.getByLabel('计划采用的框架').fill('PyTorch');
    await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.getByText('版本 2').waitFor();
    // Exercise the same SSE and final-result paths used by real reading tasks.
    const citation = '97e04e63-f2ef-527b-ad9a-30bc4c1f51da';
    const markdown = `## Reading summary\n\nA **clear method** with evidence [cite:${citation}].\n\n- First finding\n- Second finding\n\n| Method | Result |\n| --- | --- |\n| Baseline | 1 |\n\n\`\`\`python\nprint('**literal**')\n\`\`\`\n\n<img src=x onerror="window.mdUnsafe=true"><script>window.mdUnsafe=true</script>\n\n[unsafe](javascript:alert(1))\n\n\`[cite:${citation}]\``;
    let completed = false;
    let noteVersions = [{version:1,body:markdown,feedback:'',approved:0}];
    await page.route('**/api/tasks/markdown-fixture', route => route.fulfill({json:{
      id:'markdown-fixture',kind:'reading',status:completed ? 'succeeded' : 'running',
      stage:completed ? 'done' : 'generating',session_id:321,
      payload:{paper_ids:[1],profile:{revision:2}},
      result:completed ? {answer:markdown,citations:[],usage:{},evidence_review:{
        status:'completed',automatic:true,evaluated_units:1,total_units:1,
        claims:[{text:'A clear method with evidence.',verdict:'supported',reason:'Synthetic UI check only.',
          quotes:{[citation]:'Verified fixture evidence.'}}],
      }} : null,
    }}));
    await page.route('**/api/tasks/markdown-fixture/notes', route => {
      if (route.request().method() === 'PUT') {
        noteVersions.unshift({...route.request().postDataJSON(),version:noteVersions.length+1});
      }
      return route.fulfill({json:completed ? noteVersions : []});
    });
    await page.route('**/api/tasks/markdown-fixture/events', route => route.fulfill({
      contentType:'text/event-stream',
      body:`event: stage\ndata: ${JSON.stringify({stage:'generating'})}\n\n` +
        `event: delta\ndata: ${JSON.stringify({text:markdown.slice(0,30)})}\n\n` +
        `event: delta\ndata: ${JSON.stringify({text:markdown.slice(30)})}\n\n`,
    }));
    await page.route(`**/api/chunks/${citation}`, route => route.fulfill({json:{
      paper_id:1,page:3,text:'Verified fixture evidence.',pdf_url:'about:blank',
    }}));
    await page.evaluate(() => localStorage.setItem('litagent-last-task','markdown-fixture'));
    await page.reload({waitUntil:'networkidle'});
    if (await page.getByLabel('可用算力').inputValue() !== 'Single GPU, 12 GB')
      throw new Error('Extended profile was not persisted');
    await page.getByText('论文阅读', {exact:true}).click();
    await page.getByLabel('选择阅读论文').selectOption('1');
    await page.getByRole('tab',{name:'阅读笔记',exact:true}).click();
    const result = page.locator('#reading-result');
    await result.locator('h2').filter({hasText:'Reading summary'}).waitFor();
    if (await result.locator('strong').textContent() !== 'clear method' ||
        await result.locator('ul li').count() !== 2 ||
        await result.locator('table tbody tr').count() !== 1 ||
        await result.locator('.citation-button').count() !== 1 ||
        await result.locator('script, img, [onerror], a[href^="javascript:"]').count() !== 0 ||
        await page.evaluate(() => Boolean(window.mdUnsafe))) throw new Error('Streaming Markdown rendering failed');
    if (!(await result.locator('pre code').textContent()).includes('**literal**'))
      throw new Error('Code block was incorrectly parsed as Markdown');
    await result.locator('.citation-button').click();
    await page.getByText('Verified fixture evidence.', {exact:true}).waitFor();
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    completed = true;
    await page.locator('#run-state').filter({hasText:'已完成'}).waitFor();
    await page.locator('#note-versions option').first().waitFor({state:'attached'});
    await result.locator('h2').waitFor();
    await page.locator('#evidence-review summary').click();
    await page.locator('#evidence-review-status').filter({hasText:'1/1'}).waitFor();
    await page.locator('#evidence-review .citation-button').click();
    await page.getByText('Verified fixture evidence.', {exact:true}).last().waitFor();
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('button',{name:'专注阅读',exact:true}).click();
    if (await page.locator('.reading-input').isVisible()) throw new Error('Focus mode failed');
    await page.getByRole('button',{name:'退出专注阅读',exact:true}).click();
    if (!(await page.locator('.reading-input').isVisible())) throw new Error('Focus mode did not restore controls');
    await page.screenshot({path:path.join(screenshots,'markdown-desktop.png')});
    await page.locator('.main').evaluate(node => { node.scrollTop = 0; });
    await page.screenshot({path:path.join(screenshots,'desktop.png')});
    let desktop = await page.evaluate(() => ({
      viewport: innerWidth, page: document.documentElement.scrollWidth,
      panel: getComputedStyle(document.querySelector('#panel5')).display,
      title: document.querySelector('main>h1').textContent,
      visibleHeading: document.querySelector('#panel5 h2').getBoundingClientRect().width,
    }));
    if (desktop.panel === 'none' || desktop.visibleHeading <= 0 || desktop.page > desktop.viewport + 2)
      throw new Error(`Desktop layout failed: ${JSON.stringify(desktop)}`);
    for (const width of [1024,768]) {
      await page.setViewportSize({width,height:1000});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
      if (overflow) throw new Error(`Layout overflow at ${width}px`);
    }
    await page.setViewportSize({width:390,height:844});
    await page.reload({waitUntil:'networkidle'});
    await page.getByText('论文阅读', {exact:true}).click();
    await page.getByRole('button',{name:'打开文献管理',exact:true}).click();
    if (!(await page.locator('#sidebar').isVisible())) throw new Error('Mobile library drawer did not open');
    await page.getByRole('button',{name:'关闭文献管理',exact:true}).click({position:{x:350,y:100}});
    if (await page.locator('#sidebar').isVisible()) throw new Error('Mobile library drawer did not close');
    await page.evaluate(() => scrollTo(0,0));
    await page.screenshot({path:path.join(screenshots,'mobile.png')});
    await result.locator('h2').waitFor();
    await page.screenshot({path:path.join(screenshots,'markdown-mobile.png'),fullPage:true});
    const mobile = await page.evaluate(() => ({
      viewport: innerWidth, page: document.documentElement.scrollWidth,
      panel: getComputedStyle(document.querySelector('#panel5')).display,
      toolbar: document.querySelector('.reading-toolbar').getBoundingClientRect().width,
    }));
    if(mobile.panel==='none' || mobile.page > mobile.viewport + 2)
      throw new Error(`Mobile layout failed: ${JSON.stringify(mobile)}`);
    await page.setViewportSize({width:1440,height:1000});
    const submitted = [];
    await page.route('**/api/chat/runs', route => {
      submitted.push(route.request().postDataJSON());
      return route.fulfill({json:{id:'markdown-fixture'}});
    });
    await page.locator('[data-action=question]').click();
    await page.locator('#reading-message').fill('How does this method work?');
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/notes')),
      page.getByRole('button',{name:'开始阅读',exact:true}).click(),
    ]);
    if (submitted[0]?.session_id !== 321) throw new Error('Follow-up did not retain reading session');
    await page.getByRole('button',{name:'新建阅读对话',exact:true}).click();
    await page.locator('#reading-message').fill('Start a fresh question');
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/notes')),
      page.getByRole('button',{name:'开始阅读',exact:true}).click(),
    ]);
    if (submitted[1]?.session_id !== null) throw new Error('New reading session reused old context');
    await page.locator('#note-editor summary').click();
    await page.locator('#note-body').fill(`## Edited note\n\nAn edited finding with its evidence [cite:${citation}].`);
    await page.getByRole('button',{name:'保存新版本',exact:true}).click();
    await result.locator('h2').filter({hasText:'Edited note'}).waitFor();
    if (await page.locator('#evidence-review').isVisible())
      throw new Error('Original evidence review was incorrectly applied to an edited note');
    await page.reload({waitUntil:'networkidle'});
    await result.locator('h2').filter({hasText:'Edited note'}).waitFor();
    if (await page.locator('#evidence-review').isVisible())
      throw new Error('Reload incorrectly restored the original review for an edited note');
    await page.getByRole('button',{name:'论文对比',exact:true}).click();
    // Keep an empty A slot stable during list refresh, then submit in explicit A/B order.
    await page.getByLabel('论文 A',{exact:true}).selectOption('');
    await page.getByLabel('论文 B',{exact:true}).selectOption('1');
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/tasks')),
      page.getByRole('button',{name:'刷新论文与任务',exact:true}).click(),
    ]);
    if (await page.getByLabel('论文 A',{exact:true}).inputValue() !== '' ||
        await page.getByLabel('论文 B',{exact:true}).inputValue() !== '1')
      throw new Error('Refresh moved a selected paper into the other comparison slot');
    await page.getByLabel('论文 A',{exact:true}).selectOption('2');
    await page.route('**/api/tasks/comparison-fixture', route => route.fulfill({json:{
      id:'comparison-fixture',kind:'reading',status:'succeeded',stage:'done',session_id:322,
      payload:{paper_ids:[2,1],skill_id:'compare',profile:{revision:2}},
      result:{answer:'## Comparison fixture\n\nComparison test output.',citations:[],usage:{}},
    }}));
    await page.route('**/api/tasks/comparison-fixture/notes', route => route.fulfill({json:[]}));
    await page.route('**/api/chat/runs', route => {
      submitted.push(route.request().postDataJSON());
      return route.fulfill({json:{id:'comparison-fixture'}});
    });
    await page.locator('#reading-message').fill('Compare the methods and evidence under matching conditions.');
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/comparison-fixture/notes')),
      page.getByRole('button',{name:'开始阅读',exact:true}).click(),
    ]);
    const comparison = submitted.at(-1);
    if (comparison.skill_id !== 'compare' || JSON.stringify(comparison.paper_ids) !== '[2,1]' ||
        comparison.session_id !== null)
      throw new Error('Comparison submission lost its two papers, order, or fresh context');
    await page.reload({waitUntil:'networkidle'});
    await result.locator('h2').filter({hasText:'Comparison fixture'}).waitFor();
    if (!(await page.locator('#compare-paper-selection').isVisible()) ||
        await page.getByLabel('论文 A',{exact:true}).inputValue() !== '2' ||
        await page.getByLabel('论文 B',{exact:true}).inputValue() !== '1')
      throw new Error('Reload did not restore comparison mode and both papers');
    if(pageErrors.length) throw new Error(`Browser errors: ${pageErrors.join('; ')}`);
    console.log(JSON.stringify({desktop,mobile,pageErrors,api:'passed',screenshots}));
  } finally {
    await browser?.close();
    await stopServer(server);
    const resolvedScratch = fs.realpathSync(scratch);
    const resolvedRoot = fs.realpathSync(root);
    if (path.dirname(resolvedScratch).toLowerCase() !== resolvedRoot.toLowerCase() ||
        !path.basename(resolvedScratch).startsWith('.browser-test-')) {
      throw new Error('Refusing cleanup outside the browser-test workspace.');
    }
    fs.rmSync(scratch,{recursive:true,force:true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
