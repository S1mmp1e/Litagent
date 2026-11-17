const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const {pythonPath, browserOptions, stopServer} = require('./browser-support.cjs');

const root = path.resolve(__dirname, '..');
const scratch = fs.mkdtempSync(path.join(root, '.browser-test-settings-'));
const output = path.join(root, 'artifacts', 'ui');
const port = 17872, base = `http://127.0.0.1:${port}`;
const key = 'synthetic-browser-settings-key';

async function main() {
  let providerStatus = 200;
  const providerRequests = [];
  const provider = http.createServer((request, response) => {
    providerRequests.push({method:request.method, path:request.url});
    response.writeHead(providerStatus, {'Content-Type':'application/json'});
    response.end(providerStatus === 200
      ? JSON.stringify({data:[{id:'demo-model'},{id:'demo-model-two'}]})
      : JSON.stringify({error:key}));
  });
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve));
  const providerUrl = `http://127.0.0.1:${provider.address().port}/v1`;
  const server = spawn(pythonPath(root), ['-m','lit_agent.webui'], {
    cwd:root, windowsHide:true, stdio:'ignore',
    env:{...process.env, LIT_PORT:String(port), LIT_DB_PATH:path.join(scratch,'test.db'),
      LIT_DATA_DIR:path.join(scratch,'data'), DEEPSEEK_API_KEY:'', LIT_API_KEY:'',
      LIT_BASE_URL:'https://api.deepseek.com', LIT_MODEL:'deepseek-flash'},
  });
  let browser;
  try {
    let ready = false;
    for (let i=0; i<100; i++) {
      if (server.exitCode !== null) throw new Error('Model settings server exited');
      try { if ((await fetch(base+'/api/health')).ok) { ready=true; break; } } catch {}
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    assert(ready, 'Model settings server did not start');
    browser = await chromium.launch(browserOptions());
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [], taskRequests = [], configResponses = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.url().endsWith('/api/chat/runs')) taskRequests.push(request.method());
    });
    page.on('response', response => {
      if (response.url().includes('/api/settings/model')) configResponses.push(response.text());
    });
    await page.goto(base, {waitUntil:'networkidle'});
    await page.locator('#model-setup-banner').waitFor({state:'visible'});
    fs.mkdirSync(output, {recursive:true});
    await page.screenshot({path:path.join(output,'model-setup-desktop.png')});
    await page.getByRole('button',{name:'配置 API',exact:true}).click();
    const dialog = page.getByRole('dialog',{name:'模型设置',exact:true});
    await page.locator('#model-api-key').waitFor({state:'visible'});
    await page.waitForFunction(() => !document.getElementById('model-settings-fields').disabled);
    assert.equal(await page.getByLabel('API 地址',{exact:true}).inputValue(), 'https://api.deepseek.com');
    await page.getByLabel('API 地址',{exact:true}).fill(providerUrl);
    await page.getByLabel('模型名称',{exact:true}).fill('demo-model');
    await page.getByLabel('API Key',{exact:true}).fill(key);
    await page.getByRole('button',{name:'显示本次输入的密钥',exact:true}).click();
    assert.equal(await page.locator('#model-api-key').getAttribute('type'), 'text');
    await page.getByRole('button',{name:'隐藏本次输入的密钥',exact:true}).click();
    await page.getByRole('button',{name:'测试连接',exact:true}).click();
    await page.locator('#model-settings-result').filter({hasText:'连接成功'}).waitFor();
    assert.equal(await page.locator('#model-api-options option').count(), 2);
    assert.equal((await (await fetch(base+'/api/health')).json()).api_configured, false);
    assert(!fs.existsSync(path.join(scratch,'data','model-config.json')));
    assert.deepEqual(providerRequests, [{method:'GET',path:'/v1/models'}]);
    await dialog.screenshot({path:path.join(output,'model-settings-desktop.png')});
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await page.locator('#model-settings-result').filter({hasText:'已保存'}).waitFor();
    assert.equal(await page.getByLabel('API Key',{exact:true}).inputValue(), '');
    assert.equal((await (await fetch(base+'/api/health')).json()).model, 'demo-model');
    assert.equal(JSON.parse(fs.readFileSync(path.join(scratch,'data','model-config.json'))).api_key, key);
    await page.getByRole('button',{name:'关闭模型设置',exact:true}).click();
    await page.reload({waitUntil:'networkidle'});
    assert.equal(await page.locator('#model-setup-banner').isVisible(), false);
    await page.getByRole('button',{name:'模型设置',exact:true}).click();
    await page.waitForFunction(() => !document.getElementById('model-settings-fields').disabled);
    assert.equal(await page.getByLabel('API Key',{exact:true}).inputValue(), '');
    assert.match(await page.getByLabel('API Key',{exact:true}).getAttribute('placeholder'), /留空/);
    await page.getByLabel('模型名称',{exact:true}).fill('demo-model-two');
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await page.locator('#model-settings-result').filter({hasText:'已保存'}).waitFor();
    assert.equal((await (await fetch(base+'/api/health')).json()).model, 'demo-model-two');
    assert.equal(JSON.parse(fs.readFileSync(path.join(scratch,'data','model-config.json'))).api_key, key);
    providerStatus = 401;
    await page.getByRole('button',{name:'测试连接',exact:true}).click();
    await page.locator('#model-settings-result').filter({hasText:'认证失败'}).waitFor();
    assert(!(await dialog.textContent()).includes(key));
    providerStatus = 200;
    await page.getByLabel('模型名称',{exact:true}).fill('unavailable-model');
    await page.getByRole('button',{name:'测试连接',exact:true}).click();
    await page.locator('#model-settings-result').filter({hasText:'不在可用列表'}).waitFor();
    await page.getByLabel('API Key',{exact:true}).fill('unsaved-key');
    await page.getByRole('button',{name:'关闭模型设置',exact:true}).click();
    await page.getByRole('button',{name:'模型设置',exact:true}).click();
    await page.waitForFunction(() => !document.getElementById('model-settings-fields').disabled);
    assert.equal(await page.getByLabel('API Key',{exact:true}).inputValue(), '');
    assert.equal(await page.getByLabel('模型名称',{exact:true}).inputValue(), 'demo-model-two');
    for (const width of [390,320]) {
      await page.setViewportSize({width,height:760});
      const geometry = await dialog.evaluate(node => {
        const rect = node.getBoundingClientRect();
        return {left:rect.left,right:rect.right,bottom:rect.bottom,scroll:node.scrollWidth,width:node.clientWidth,
          viewport:innerWidth,height:innerHeight};
      });
      assert(geometry.left >= 0 && geometry.right <= width && geometry.scroll <= geometry.width+1,
        JSON.stringify(geometry));
      assert(geometry.bottom <= geometry.height, JSON.stringify(geometry));
      await dialog.screenshot({path:path.join(output,`model-settings-mobile-${width}.png`)});
    }
    await page.setViewportSize({width:1440,height:1000});
    page.once('dialog', popup => popup.accept());
    await page.getByRole('button',{name:'恢复环境配置',exact:true}).click();
    await page.locator('#model-settings-result').filter({hasText:'已恢复环境配置'}).waitFor();
    assert(!fs.existsSync(path.join(scratch,'data','model-config.json')));
    await page.getByRole('button',{name:'关闭模型设置',exact:true}).click();
    await page.locator('#model-setup-banner').waitFor({state:'visible'});
    await page.locator('label[for=tab4]').click();
    await page.locator('#chat-input').fill('Keep my question while configuring');
    await page.locator('#send-btn').click();
    await dialog.waitFor({state:'visible'});
    await page.locator('#model-settings-result').filter({hasText:'填写并保存'}).waitFor();
    assert.equal(await page.locator('#chat-input').inputValue(), 'Keep my question while configuring');
    assert.deepEqual(taskRequests, []);
    const storage = await page.evaluate(() => JSON.stringify({local:{...localStorage},session:{...sessionStorage}}));
    assert(!storage.includes(key) && !storage.includes('unsaved-key'));
    for (const body of await Promise.all(configResponses)) assert(!body.includes(key));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({settings:'passed',offlineProvider:true,
      desktop:true,mobile:[390,320],keyPersistence:true,secretResponses:false,pageErrors:errors,screenshots:output}));
  } finally {
    await browser?.close();
    await stopServer(server);
    await new Promise(resolve => provider.close(resolve));
    const resolved = fs.realpathSync(scratch);
    assert.equal(path.dirname(resolved).toLowerCase(), fs.realpathSync(root).toLowerCase());
    assert(path.basename(resolved).startsWith('.browser-test-settings-'));
    fs.rmSync(resolved, {recursive:true,force:true});
  }
}

main().catch(error => { console.error(error); process.exitCode=1; });
