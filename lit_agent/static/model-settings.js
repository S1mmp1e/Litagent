/* Credentials live only in the password field until explicitly sent to the local server. */
(() => {
  const el = id => document.getElementById(id);
  const dialog = el('model-settings-dialog');
  const icons = () => window.lucide?.createIcons();
  let state = null, busy = false, openToken = 0;

  async function request(method = 'GET', body, suffix = '') {
    const response = await fetch('/api/settings/model' + suffix, {
      method, cache:'no-store',
      headers:{'Content-Type':'application/json', 'X-LitAgent-Settings':'1'},
      ...(body ? {body:JSON.stringify(body)} : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '模型配置请求失败。');
    return data;
  }
  function status(message = '', type = '') {
    el('model-settings-result').textContent = message;
    el('model-settings-result').dataset.state = type;
  }
  function update(data) {
    state = data;
    el('model-status').textContent = `${data.model} · ${data.api_configured ? 'API 已配置' : 'API 未配置'}`;
    el('model-status').dataset.configured = String(data.api_configured);
    el('model-setup-banner').hidden = data.api_configured;
    el('model-setup-message').textContent = data.error || '尚未配置 API Key，阅读与 AI 对话需要模型服务。';
    el('model-config-source').textContent = {
      local:'使用界面保存的本机配置',
      environment:'读取 .env 或进程环境配置',
      default:'尚未保存模型配置',
    }[data.source] || '';
    el('reset-model-settings').disabled = busy || !data.can_reset;
  }
  function hideKey() {
    el('model-api-key').type = 'password';
    const button = el('toggle-model-key');
    button.setAttribute('aria-pressed', 'false');
    button.title = '显示本次输入的密钥';
    button.setAttribute('aria-label', button.title);
    button.innerHTML = '<i data-lucide="eye"></i>';
    icons();
  }
  function fill(data) {
    el('model-api-base').value = data.base_url;
    el('model-api-name').value = data.model;
    el('model-api-key').value = '';
    el('model-api-key').placeholder = data.api_configured ? '已配置；留空保留原密钥' : '填写你自己的 API Key';
    hideKey();
    el('model-api-options').replaceChildren();
  }
  function setBusy(value) {
    busy = value;
    el('model-settings-fields').disabled = value;
    el('reset-model-settings').disabled = value || !state?.can_reset;
    el('close-model-settings').disabled = value;
    dialog.setAttribute('aria-busy', String(value));
  }
  function draft() {
    return {base_url:el('model-api-base').value.trim(), model:el('model-api-name').value.trim(),
      api_key:el('model-api-key').value.trim()};
  }
  async function refresh() {
    const data = await request();
    update(data);
    return data;
  }
  async function open() {
    const token = ++openToken;
    el('model-api-key').value = '';
    status('读取配置中…');
    setBusy(true);
    if (!dialog.open) dialog.showModal();
    try {
      const data = await refresh();
      if (token !== openToken || !dialog.open) return;
      fill(data);
      status(data.error || '');
      setBusy(false);
      el('model-api-key').focus();
    } catch (error) {
      status(error.message, 'error');
    } finally {
      if (token === openToken) setBusy(false);
    }
  }
  async function act(action) {
    if (busy || !el('model-settings-form').reportValidity()) return;
    setBusy(true);
    status(action === 'test' ? '正在连接模型服务…' : '保存中…');
    try {
      const data = await request(action === 'test' ? 'POST' : 'PUT', draft(), action === 'test' ? '/test' : '');
      if (action === 'test') {
        el('model-api-options').replaceChildren(...data.models.map(model => new Option(model, model)));
        status(data.message + ' 测试未保存配置。', data.ok ? 'success' : 'warning');
      } else {
        update(data); fill(data);
        status('已保存。新任务立即生效，正在执行的任务继续使用原配置。', 'success');
        document.dispatchEvent(new CustomEvent('lit-model-config-change'));
      }
    } catch (error) { status(error.message, 'error'); }
    finally { setBusy(false); }
  }
  el('open-model-settings').addEventListener('click', open);
  el('setup-model-now').addEventListener('click', open);
  el('close-model-settings').addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => {
    openToken += 1; el('model-api-key').value = ''; hideKey(); status('');
  });
  el('toggle-model-key').addEventListener('click', () => {
    const visible = el('model-api-key').type === 'password';
    el('model-api-key').type = visible ? 'text' : 'password';
    const button = el('toggle-model-key');
    button.setAttribute('aria-pressed', String(visible));
    button.title = visible ? '隐藏本次输入的密钥' : '显示本次输入的密钥';
    button.setAttribute('aria-label', button.title);
    button.innerHTML = `<i data-lucide="${visible ? 'eye-off' : 'eye'}"></i>`;
    icons();
  });
  el('model-settings-form').addEventListener('submit', event => { event.preventDefault(); act('save'); });
  el('test-model-connection').addEventListener('click', () => act('test'));
  for (const id of ['model-api-base','model-api-name','model-api-key']) {
    el(id).addEventListener('input', () => {
      status('有未保存的修改。');
      if (id === 'model-api-base') el('model-api-options').replaceChildren();
    });
  }
  el('reset-model-settings').addEventListener('click', async () => {
    if (busy || !confirm('删除界面保存的模型配置，恢复 .env 或进程环境配置？原 .env 文件不会改动。')) return;
    setBusy(true);
    try {
      const data = await request('DELETE');
      update(data); fill(data);
      status('已恢复环境配置。', 'success');
      document.dispatchEvent(new CustomEvent('lit-model-config-change'));
    } catch (error) { status(error.message, 'error'); }
    finally { setBusy(false); }
  });
  const ready = refresh().catch(error => {
    el('model-setup-banner').hidden = false;
    el('model-setup-message').textContent = error.message;
  });
  window.LitModelSettings = {
    ready, open, refresh,
    async ensureConfigured() {
      const data = await refresh();
      if (data.api_configured) return true;
      await open();
      status('填写并保存 API Key 后，再开始阅读或对话。', 'warning');
      return false;
    },
  };
})();
