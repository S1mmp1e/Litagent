const assert = require('node:assert/strict');
const {spawn,spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {pythonPath, browserOptions, stopServer} = require('./browser-support.cjs');

const root = path.resolve(__dirname,'..');
const scratch = fs.mkdtempSync(path.join(root,'.notebook-test-annotations-'));
const output = path.join(root,'artifacts','ui');
const python = pythonPath(root);
const port = 17871, base = `http://127.0.0.1:${port}`;
const env = {...process.env,LIT_PORT:String(port),LIT_DB_PATH:path.join(scratch,'test.db'),
  LIT_DATA_DIR:path.join(scratch,'data'),DEEPSEEK_API_KEY:'',LIT_API_KEY:''};

async function main(){
  fs.mkdirSync(output,{recursive:true});
  const fixture=spawnSync(python,['-X','utf8','-m','tests.notebook_fixture'],{cwd:root,env,encoding:'utf8',windowsHide:true});
  assert.equal(fixture.status,0,fixture.stderr||fixture.stdout);
  const seeded=JSON.parse(fixture.stdout);
  const server=spawn(python,['-X','utf8','-m','tests.personal_server'],{cwd:root,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let logs='',browser;
  server.stdout.on('data',data=>logs+=data);server.stderr.on('data',data=>logs+=data);
  try{
    let ready=false;
    for(let i=0;i<150;i++){
      if(server.exitCode!==null)throw new Error(logs);
      try{if((await fetch(base+'/api/health')).ok){ready=true;break;}}catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    assert(ready,logs||'Test server did not start');
    browser=await chromium.launch(browserOptions());
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',error=>errors.push(error.stack||error.message));
    await page.goto(base,{waitUntil:'networkidle'});
    await page.locator('label[for=tab5]').click();
    const paper=page.getByLabel('选择阅读论文');
    await paper.selectOption(String(seeded.papers[0]));
    const pdf=page.locator('#reading-pdf-preview');
    await page.waitForFunction(()=>document.querySelector('#reading-pdf-preview').dataset.state==='ready');
    async function selectText(){
      await pdf.locator('.textLayer span').filter({hasText:'The method uses a graph encoder'}).evaluate(span=>{
        const range=document.createRange();range.selectNodeContents(span);
        const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
        span.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
      });
      await pdf.getByRole('button',{name:'添加批注',exact:true}).click();
    }
    await selectText();
    await pdf.getByLabel('个人评论',{exact:true}).fill('我需要使用局部观测，请检查部署时的信息需求。');
    await pdf.getByLabel('绿色',{exact:true}).check();
    assert(await pdf.getByLabel('用于后续问答',{exact:true}).isChecked());
    await pdf.getByRole('button',{name:'保存批注',exact:true}).click();
    await pdf.locator('.annotation-item').waitFor();
    const item=pdf.locator('.annotation-item').first();
    const annotationId=await item.getAttribute('data-annotation-id');
    const marker=pdf.locator('.annotation-highlight').first();
    await marker.waitFor();
    const before=await marker.boundingBox();
    const ratio=await marker.evaluate(node=>parseFloat(node.style.left)/node.parentElement.clientWidth);
    await pdf.getByRole('button',{name:'放大',exact:true}).click();
    await page.waitForFunction(width=>parseFloat(document.querySelector('#reading-pdf-preview .annotation-highlight').style.width)>width+1,before.width);
    const afterRatio=await marker.evaluate(node=>parseFloat(node.style.left)/node.parentElement.clientWidth);
    assert(Math.abs(afterRatio-ratio)<0.002,'Highlight moved relative to the PDF');
    await pdf.getByRole('button',{name:'下一页',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#reading-pdf-preview').dataset.page==='2');
    assert.equal(await pdf.locator('.annotation-highlight').count(),0);
    await item.getByRole('button',{name:'定位批注',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#reading-pdf-preview').dataset.page==='1');
    await marker.waitFor();
    await item.getByRole('button',{name:'编辑批注',exact:true}).click();
    await pdf.getByLabel('个人评论',{exact:true}).fill('只使用局部观测，方法必须支持分布式执行。');
    await pdf.getByRole('button',{name:'保存批注',exact:true}).click();
    await item.filter({hasText:'v2'}).waitFor();
    await item.getByRole('button',{name:'编辑批注',exact:true}).click();
    await pdf.getByLabel('批注历史版本').waitFor({state:'visible'});
    await pdf.getByLabel('批注历史版本').selectOption('1');
    assert.match(await pdf.getByLabel('个人评论',{exact:true}).inputValue(),/我需要使用局部观测/);
    await pdf.getByRole('button',{name:'取消',exact:true}).click();
    await pdf.getByRole('button',{name:'适应宽度',exact:true}).click();
    await page.waitForFunction(()=>{
      const pdf=document.querySelector('#reading-pdf-preview');
      return pdf.querySelector('.pdf-page').clientWidth<=pdf.querySelector('.pdf-viewport').clientWidth;
    });
    await page.screenshot({path:path.join(output,'annotations-desktop.png')});

    await paper.selectOption(String(seeded.papers[1]));
    await page.waitForFunction(id=>document.querySelector('#reading-pdf-preview').dataset.paperId===String(id)&&document.querySelector('#reading-pdf-preview').dataset.state==='ready',seeded.papers[1]);
    assert.equal(await pdf.locator('.annotation-highlight').count(),0);
    await paper.selectOption(String(seeded.papers[0]));
    await marker.waitFor();
    await page.reload({waitUntil:'networkidle'});
    await page.locator('label[for=tab5]').click();
    await paper.selectOption(String(seeded.papers[0]));
    await page.getByRole('tab',{name:'PDF 原文',exact:true}).click();
    await marker.waitFor();

    await page.locator('#use-personal').check();
    await page.locator('#verify-evidence').uncheck();
    await page.locator('[data-action=question]').click();
    await page.locator('#reading-message').fill('结合我的批注解释方法的信息需求。');
    await page.locator('#run-reading').click();
    await page.locator('#run-state').filter({hasText:'已完成'}).waitFor({timeout:30000});
    await page.locator('#reading-result .personal-citation-button').first().click();
    await page.locator('.personal-evidence-dialog[open]').waitFor();
    assert.match(await page.locator('.personal-evidence-text').textContent(),/局部观测/);
    await page.getByRole('button',{name:'关闭个人材料',exact:true}).click();
    await page.screenshot({path:path.join(output,'personal-answer-desktop.png')});

    await page.locator('label[for=tab6]').click();
    await page.locator(`.notebook-item[data-task-id="${seeded.notes[0]}"]`).click();
    const included=page.locator('#notebook-personal');
    assert(!(await included.isChecked()));
    await included.check();
    await page.waitForFunction(()=>!document.querySelector('#notebook-personal').disabled);
    let detail=await (await page.request.get(base+'/api/notes/'+seeded.notes[0])).json();
    assert.equal(detail.retrieval_version,1);
    await page.getByRole('button',{name:'编辑笔记',exact:true}).click();
    await page.locator('#notebook-editor').fill((await page.locator('#notebook-editor').inputValue())+'\n\n新的个人判断。');
    await page.locator('#notebook-save').click();
    await page.locator('#notebook-body').filter({hasText:'新的个人判断'}).waitFor();
    assert(!(await included.isChecked()),'Edited versions need fresh opt-in');
    await page.getByLabel('笔记历史版本').selectOption('1');
    assert(await included.isDisabled());
    await page.getByLabel('笔记历史版本').selectOption('2');
    await included.check();
    await page.waitForFunction(()=>!document.querySelector('#notebook-personal').disabled);
    detail=await (await page.request.get(base+'/api/notes/'+seeded.notes[0])).json();
    assert.equal(detail.retrieval_version,2);

    // A delayed annotation list from A must never paint on B.
    await page.locator('label[for=tab5]').click();
    await page.getByRole('tab',{name:'PDF 原文',exact:true}).click();
    await paper.selectOption(String(seeded.papers[1]));
    await page.waitForFunction(()=>document.querySelector('#reading-pdf-preview').dataset.state==='ready');
    let release,started;
    const gate=new Promise(resolve=>release=resolve), requested=new Promise(resolve=>started=resolve);
    await page.route(`**/api/annotations?paper_id=${seeded.papers[0]}&*`,async route=>{started();await gate;await route.continue();});
    await paper.selectOption(String(seeded.papers[0]));await requested;
    await paper.selectOption(String(seeded.papers[1]));
    release();
    await page.waitForFunction(id=>document.querySelector('#reading-pdf-preview').dataset.paperId===String(id)&&document.querySelector('#reading-pdf-preview').dataset.state==='ready',seeded.papers[1]);
    await page.unroute(`**/api/annotations?paper_id=${seeded.papers[0]}&*`);
    assert.equal(await pdf.locator('.annotation-highlight').count(),0);
    assert.equal(await pdf.locator('.annotation-item').count(),0);
    await paper.selectOption(String(seeded.papers[0]));
    await marker.waitFor();
    await pdf.getByRole('button',{name:'批注列表',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>{
      const pdf=document.querySelector('#reading-pdf-preview');
      return pdf.querySelector('.pdf-page').clientWidth<=pdf.querySelector('.pdf-viewport').clientWidth;
    });
    await pdf.locator('.annotations-panel').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,'annotations-mobile.png')});
    const horizontalOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);
    assert(!horizontalOverflow,'Mobile page overflows horizontally');
    const bounds=await pdf.locator('.annotation-editor').evaluate(node=>node.closest('.pdf-preview').getBoundingClientRect().width);
    assert(bounds<390,'PDF preview exceeds mobile viewport');
    page.once('dialog',dialog=>dialog.accept());
    await pdf.getByRole('button',{name:'删除批注',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('#reading-pdf-preview .annotation-item').length===0);
    assert.equal(await pdf.locator('.annotation-highlight').count(),0);
    assert.equal((await page.request.get(base+'/api/annotations/'+annotationId)).status(),404);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({annotations:'passed',zoom:'passed',restore:'passed',sourceIsolation:'passed',
      offlineReading:'passed',noteOptIn:'passed',mobile:'passed',errors,screenshots:output}));
  }finally{
    await browser?.close();
    await stopServer(server);
    const target=fs.realpathSync(scratch),workspace=fs.realpathSync(root);
    if(path.dirname(target).toLowerCase()!==workspace.toLowerCase()||!path.basename(target).startsWith('.notebook-test-'))
      throw new Error('Refusing cleanup outside test workspace');
    fs.rmSync(target,{recursive:true,force:true,maxRetries:8,retryDelay:250});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
