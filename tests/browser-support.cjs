const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

function pythonPath(root) {
  return process.env.LIT_TEST_PYTHON || path.join(root, '.venv',
    ...(process.platform === 'win32' ? ['Scripts','python.exe'] : ['bin','python']));
}

function browserOptions() {
  const edge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const executablePath = process.env.LIT_BROWSER_PATH ||
    (process.platform === 'win32' && fs.existsSync(edge) ? edge : undefined);
  return {headless:true, ...(executablePath ? {executablePath} : {})};
}

async function stopServer(server) {
  if (server.exitCode !== null || server.signalCode !== null) return;
  if (process.platform === 'win32') {
    const stopped = spawnSync('taskkill', ['/PID',String(server.pid),'/T','/F'],
      {windowsHide:true,stdio:'ignore',timeout:5000});
    if (stopped.status !== 0) server.kill();
  } else server.kill('SIGTERM');
  if (server.exitCode !== null || server.signalCode !== null) return;
  await new Promise((resolve,reject) => {
    const timer = setTimeout(() => {
      server.kill('SIGKILL');
      reject(new Error(`Test server ${server.pid} did not stop`));
    }, 10000);
    server.once('exit', () => {clearTimeout(timer);resolve();});
  });
}

async function mockModelSettings(page) {
  // These reading suites mock generation; credential setup is exercised by model-settings-browser.cjs.
  await page.route('**/api/settings/model', route => route.fulfill({json:{
    base_url:'https://api.deepseek.com',model:'synthetic-test-model',
    api_configured:true,source:'environment',can_reset:false,error:null,
  }}));
}

module.exports = {pythonPath, browserOptions, stopServer, mockModelSettings};
