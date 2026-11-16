/* Single-user reading workspace. Only server-issued identifiers are persisted in the browser. */
(() => {
  const el = id => document.getElementById(id);
  const terminal = new Set(['succeeded','failed','cancelled']);
  const states = {queued:'排队中',running:'执行中',succeeded:'已完成',failed:'失败',cancelled:'已取消'};
  const stages = {queued:'等待执行',downloading:'下载 PDF',downloaded:'下载完成',parsing:'解析 PDF',parsed:'解析完成',indexing:'建立索引',reading:'分段阅读',generating:'生成笔记',done:'完成',executing_tool:'执行工具',tool_round_complete:'工具执行完成'};
  Object.assign(stages, {rewriting:'理解追问',retrieving:'检索证据',checking_gaps:'检查材料缺口',supplementing:'定向补查',verifying:'核查引用依据'});
  stages.retrieving_personal='检索个人笔记与批注';
  const profileFields = ['background','purpose','focus','depth','language','research_domain','compute_budget','framework','known_topics'];
  let presets = {}, scope = '', lastDefault = '请总结这篇论文，解释核心方法、主要实验和局限。';
  let skill = 'overview', action = 'summary', currentTask = null, readingSession = null, source = null, noteTask = null;
  let skillSpecs = {};
  let noteVersions = [], taskTimer = null, originalResult = null;
  let contextEpoch = 0, watchedContext = 0, watchToken = 0, profileRevision = null;
  let previewPaper = null, adoptingTask = false;
  const emptyNote = el('reading-result').firstElementChild.cloneNode(true);
  const icons = () => window.lucide?.createIcons();
  function showReadingView(view) {
    for (const name of ['pdf','notes']) {
      const active = name === view, tab = el(`${name}-view-tab`);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      el(`reading-${name}-panel`).hidden = !active;
    }
  }
  for (const name of ['pdf','notes']) {
    const tab = el(`${name}-view-tab`);
    tab.addEventListener('click', () => showReadingView(name));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'pdf' : event.key === 'End' ? 'notes' : name === 'pdf' ? 'notes' : 'pdf';
      showReadingView(next); el(`${next}-view-tab`).focus();
    });
  }
  function syncPreview(ids, preferred = null) {
    previewPaper = ids.includes(preferred) ? preferred : ids.includes(previewPaper) ? previewPaper : ids[0] || null;
    const tabs = el('preview-paper-tabs');
    tabs.replaceChildren();
    tabs.hidden = ids.length < 2;
    for (const id of ids) {
      const option = [...el('reading-papers').options].find(o => Number(o.value) === id);
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = skill === 'compare' ? `论文 ${Number(el('compare-paper-a').value) === id ? 'A' : 'B'}` : `论文 ${id}`;
      button.title = option?.dataset.title || '';
      button.classList.toggle('selected', id === previewPaper);
      button.setAttribute('aria-pressed', String(id === previewPaper));
      button.addEventListener('click', () => syncPreview(ids, id));
      tabs.append(button);
    }
    const option = [...el('reading-papers').options].find(o => Number(o.value) === previewPaper);
    window.LitPDF.show('reading-pdf-preview', previewPaper, option?.dataset.title);
  }
  el('focus-reading').addEventListener('click', () => {
    const active = el('panel5').classList.toggle('reading-focused');
    const button = el('focus-reading');
    button.setAttribute('aria-pressed', String(active));
    button.title = active ? '退出专注阅读' : '专注阅读';
    button.setAttribute('aria-label', button.title);
    button.innerHTML = `<i data-lucide="${active ? 'minimize-2' : 'maximize-2'}"></i>`;
    icons();
  });
  async function request(url, options={}) {
    const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === 'string' ? error.detail : `请求失败 (${response.status})`);
    }
    return response.json();
  }
  const report = error => toast(error.message || String(error), 'error');
  const bind = (id, fn) => el(id).addEventListener('click', () => Promise.resolve().then(fn).catch(report));
  function comparisonSlots() {
    return [el('compare-paper-a').value, el('compare-paper-b').value].map(value => value ? Number(value) : null);
  }
  function selectedPapers() {
    if (skill === 'compare') return comparisonSlots().filter(Boolean);
    return [...el('reading-papers').selectedOptions].map(o => Number(o.value));
  }
  function syncCompareOptions() {
    for (const [id, other] of [['compare-paper-a','compare-paper-b'],['compare-paper-b','compare-paper-a']]) {
      for (const option of el(id).options) option.disabled = !!option.value && option.value === el(other).value;
    }
  }
  function setSelectedPapers(ids) {
    for (const option of el('reading-papers').options) option.selected = ids.includes(Number(option.value));
    el('compare-paper-a').value = ids[0] || '';
    el('compare-paper-b').value = ids[1] || '';
    syncCompareOptions();
    updatePaperSelection();
  }
  function selectSkill(next) {
    const selected = skill === 'compare' && next === 'compare' ? comparisonSlots() : selectedPapers();
    if (skill === 'compare' && next !== 'compare') selected.splice(1);
    if (skill !== next) contextEpoch += 1;
    skill = next;
    for (const button of el('skill-control').children) {
      button.classList.toggle('selected', button.dataset.skill === skill);
      button.setAttribute('aria-pressed', String(button.dataset.skill === skill));
    }
    el('single-paper-selection').hidden = skill === 'compare';
    el('compare-paper-selection').hidden = skill !== 'compare';
    setSelectedPapers(selected);
    const spec = skillSpecs[skill];
    if (spec) el('reading-message').placeholder = `${spec.name}：${spec.description}`;
    if (spec && action === 'summary') {
      if (!el('reading-message').value || el('reading-message').value === lastDefault) el('reading-message').value = spec.default_request;
      lastDefault = spec.default_request;
    }
  }
  async function loadSkills() {
    const specs = await request('/api/skills');
    skillSpecs = Object.fromEntries(specs.map(spec => [spec.id, spec]));
    const control = el('skill-control');
    control.replaceChildren();
    specs.forEach(spec => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.skill = spec.id;
      button.textContent = spec.short_name || spec.name;
      button.title = `${spec.name}：${spec.description || ''}`;
      button.setAttribute('aria-label', spec.name);
      button.classList.toggle('selected', spec.id === skill);
      button.setAttribute('aria-pressed', String(spec.id === skill));
      control.append(button);
    });
  }
  function updatePaperSelection(activatePreview = false, preferred = null) {
    const ids = selectedPapers();
    const options = ids.map(id => [...el('reading-papers').options].find(o => Number(o.value) === id)).filter(Boolean);
    el('selected-paper-title').textContent = options.map(o => {
      const label = Number(el('compare-paper-a').value) === Number(o.value) ? 'A' : 'B';
      return `${skill === 'compare' ? `${label} · ` : ''}${o.title}`;
    }).join('\n');
    const pending = options.filter(o => o.dataset.ready !== 'true').length;
    el('compare-selection-status').textContent = `已选 ${ids.length} / 2 篇${pending ? ` · ${pending} 篇待处理全文` : ''}`;
    const next = skill === 'compare' ? `compare:${comparisonSlots().join(',')}` : ids.slice().sort((a,b) => a-b).join(',');
    if (scope !== next) {
      readingSession = null; scope = next; contextEpoch += 1;
      if (!adoptingTask) {
        resetReadingOutput();
        restorePaperNote(ids).catch(report);
      }
    }
    syncPreview(ids, preferred);
    if (activatePreview && ids.length) {
      showReadingView('pdf');
      if (matchMedia('(max-width:700px)').matches) el('reading-pdf-panel').scrollIntoView({block:'start', behavior:'instant'});
    }
  }
  function filterPapers() {
    const query = el('paper-filter').value.trim().toLocaleLowerCase();
    for (const option of el('reading-papers').options) option.hidden = !option.title.toLocaleLowerCase().includes(query);
  }
  el('paper-filter').addEventListener('input', filterPapers);
  el('reading-papers').addEventListener('change', () => updatePaperSelection(true));
  for (const id of ['compare-paper-a','compare-paper-b']) {
    el(id).addEventListener('change', () => { syncCompareOptions(); updatePaperSelection(true, Number(el(id).value)); });
  }
  function toggleSidebar(open) {
    document.body.classList.toggle('sidebar-open', open);
    el('sidebar-toggle').setAttribute('aria-expanded', String(open));
    el('sidebar-backdrop').hidden = !open;
    if (open) el('sidebar').querySelector('a').focus();
    else el('sidebar-toggle').focus();
  }
  el('sidebar-toggle').addEventListener('click', () => toggleSidebar(!document.body.classList.contains('sidebar-open')));
  el('sidebar-backdrop').addEventListener('click', () => toggleSidebar(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.body.classList.contains('sidebar-open')) toggleSidebar(false);
  });
  async function loadPapers() {
    const data = await request('/api/library/rows');
    const docs = await Promise.all(data.rows.map(r => request(`/api/papers/${r[0]}/documents`)));
    const selected = skill === 'compare' ? comparisonSlots() : selectedPapers();
    el('reading-papers').replaceChildren();
    el('compare-paper-a').replaceChildren(new Option('选择第一篇论文', ''));
    el('compare-paper-b').replaceChildren(new Option('选择第二篇论文', ''));
    data.rows.forEach((r,i) => {
      const ready = docs[i].some(d => d.status === 'ready');
      const option = new Option(`${ready ? '✓' : '·'} [${r[0]}] ${r[1]}`, r[0], false, selected.includes(r[0]));
      option.title = `${r[1]} · ${ready ? '全文可读' : '尚未完成全文处理'}`;
      option.dataset.title = r[1];
      option.dataset.ready = String(ready);
      el('reading-papers').add(option);
      el('compare-paper-a').add(new Option(option.text, r[0]));
      el('compare-paper-b').add(new Option(option.text, r[0]));
    });
    filterPapers(); setSelectedPapers(selected);
  }
  async function loadProfile() {
    const profile = await request('/api/profile');
    profileRevision = profile.revision;
    for (const key of profileFields) el(`profile-${key}`).value = profile[key] || '';
    el('profile-status').textContent = `版本 ${profile.revision}`;
  }
  async function loadPresets() {
    const catalog = await request('/api/profile/presets');
    presets = Object.fromEntries(catalog.map(item => [item.id,item.profile]));
    for (const item of catalog) el('profile-preset').add(new Option(item.name,item.id));
  }
  el('profile-preset').addEventListener('change', () => {
    const preset = presets[el('profile-preset').value];
    if (!preset) return;
    for (const key of profileFields) el(`profile-${key}`).value = preset[key] || '';
    el('profile-status').textContent = '未保存';
  });
  function renderReview(review) {
    el('evidence-review').hidden = !review;
    el('evidence-review-claims').replaceChildren();
    if (!review) return;
    const labels = {supported:'支持',partial:'部分支持',insufficient:'证据不足',unverified:'未核查',uncited:'无引用',personal:'个人材料，未作论文事实核查'};
    el('evidence-review-status').textContent = review.status === 'disabled' ? '本次未启用自动核查。' :
      `自动核查 ${review.evaluated_units || 0}/${review.total_units || 0} 个段落，需人工复核。${review.reason || ''}`;
    for (const claim of review.claims || []) {
      const section = document.createElement('div');
      section.className = 'evidence-claim';
      const heading = document.createElement('strong');
      heading.textContent = labels[claim.verdict] || '未核查';
      const text = document.createElement('p');
      text.textContent = claim.text;
      const reason = document.createElement('p');
      reason.textContent = claim.reason || '';
      section.append(heading,text,reason);
      for (const [id, quote] of Object.entries(claim.quotes || {})) {
        const button = document.createElement('button');
        button.className = 'citation-button'; button.dataset.citation = id;
        button.textContent = quote;
        section.append(button);
      }
      el('evidence-review-claims').append(section);
    }
  }
  function renderAnswer(answer, citations=[]) {
    el('reading-result').dataset.personalTaskId=currentTask || '';
    window.LitMarkdown.renderInto(el('reading-result'), answer);
    el('citation-list').replaceChildren();
    citations.forEach(c => {
      const b = document.createElement('button'); b.className = 'citation-button'; b.dataset.citation = c.id;
      b.textContent = `论文 ${c.paper_id} · 第 ${c.page} 页`; el('citation-list').append(b);
    });
  }
  function resetReadingOutput() {
    watchToken += 1;
    source?.close(); source = null; clearInterval(taskTimer); taskTimer = null;
    currentTask = null; noteTask = null; originalResult = null; noteVersions = [];
    localStorage.removeItem('litagent-last-task');
    el('cancel-reading').disabled = true;
    el('note-editor').hidden = true; el('note-editor').open = false;
    el('open-saved-note').hidden = true;
    el('note-versions').replaceChildren();
    el('note-body').value = ''; el('note-feedback').value = ''; el('note-approved').checked = false;
    el('download-note').removeAttribute('href');
    el('reading-metrics').textContent = ''; el('task-progress').textContent = '';
    el('run-state').textContent = '未开始';
    renderAnswer(''); renderReview(null);
    const placeholder = emptyNote.cloneNode(true);
    placeholder.querySelector('span').textContent = selectedPapers().length ? '当前论文暂无阅读笔记' : '暂无阅读笔记';
    el('reading-result').append(placeholder);
    el('evidence-dialog').close();
    el('evidence-heading').textContent = ''; el('evidence-text').textContent = '';
    el('evidence-pdf').removeAttribute('href'); el('evidence-frame').src = 'about:blank';
  }
  async function restorePaperNote(ids) {
    if (!ids.length || (skill === 'compare' && ids.length !== 2)) return;
    const token = watchToken;
    el('task-progress').textContent = '正在加载笔记…';
    try {
      const query = new URLSearchParams();
      ids.forEach(id => query.append('paper_id', id));
      const note = await request(`/api/notes/latest?${query}`);
      if (token !== watchToken) return;
      el('task-progress').textContent = '';
      if (note) await watch(note.task_id, {restoreSelection:false, activateNotes:false});
    } catch (error) {
      if (token !== watchToken) return;
      el('task-progress').textContent = '笔记加载失败';
      throw error;
    }
  }
  async function showEvidence(id) {
    const token = watchToken;
    const chunk = await request(`/api/chunks/${id}`);
    if (token !== watchToken) return;
    el('evidence-heading').textContent = `论文 ${chunk.paper_id} · 第 ${chunk.page} 页`;
    el('evidence-text').textContent = chunk.text;
    el('evidence-pdf').href = chunk.pdf_url; el('evidence-frame').src = chunk.pdf_url;
    el('evidence-dialog').showModal();
  }
  document.addEventListener('click', e => {
    const target = e.target.closest('[data-citation]');
    if (target) showEvidence(target.dataset.citation).catch(report);
  });
  async function loadNotes(taskId, token=watchToken) {
    const versions = await request(`/api/tasks/${taskId}/notes`);
    if (token !== watchToken || currentTask !== taskId) return;
    noteVersions = versions;
    noteTask = taskId;
    el('note-editor').hidden = !noteVersions.length;
    el('open-saved-note').hidden = !noteVersions.length;
    el('note-versions').replaceChildren();
    noteVersions.forEach(n => el('note-versions').add(new Option(`版本 ${n.version}${n.approved ? ' · 已审核' : ''}`, n.version)));
    displayNote();
    el('download-note').href = `/api/tasks/${taskId}/notes/download`;
  }
  function displayNote() {
    const note = noteVersions.find(n => n.version === Number(el('note-versions').value));
    if (!note) return;
    el('note-body').value = note.body; el('note-feedback').value = note.feedback; el('note-approved').checked = !!note.approved;
    renderAnswer(note.body, originalResult?.citations || []);
    renderReview(note.body === originalResult?.answer ? originalResult.evidence_review : null);
  }
  async function loadTasks() {
    const tasks = await request('/api/tasks');
    el('reading-tasks').replaceChildren();
    for (const t of tasks) {
      const row = document.createElement('tr');
      for (const value of [new Date(t.created_at*1000).toLocaleString(), {reading:'论文阅读',ingest:'全文处理',chat:'文献管理',cleanup:'索引清理'}[t.kind] || t.kind,
        states[t.status] || t.status, t.error || stages[t.stage] || t.stage]) {
        const td = document.createElement('td'); td.textContent = value; row.append(td);
      }
      if (t.status === 'failed') row.classList.add('task-error');
      row.children[2].classList.add('task-state');
      row.children[2].dataset.state = t.status;
      const actions = document.createElement('td');
      const open = document.createElement('button'); open.className = 'btn icon-btn'; open.title = '查看任务'; open.setAttribute('aria-label','查看任务');
      open.innerHTML = '<i data-lucide="arrow-up-right"></i>'; open.onclick = () => watch(t.id).catch(report); actions.append(open);
      if (['failed','cancelled'].includes(t.status) && t.kind !== 'chat') {
        const retry = document.createElement('button'); retry.className = 'btn icon-btn'; retry.title = '重试任务'; retry.setAttribute('aria-label','重试任务');
        retry.innerHTML = '<i data-lucide="rotate-cw"></i>'; retry.onclick = () => request(`/api/tasks/${t.id}/retry`, {method:'POST'}).then(n => watch(n.id)).catch(report); actions.append(retry);
      }
      row.append(actions); el('reading-tasks').append(row);
    }
    icons();
  }
  async function finishTask(taskId, token=watchToken, loadedTask=null) {
    const task = loadedTask || await request(`/api/tasks/${taskId}`);
    if (token !== watchToken || currentTask !== taskId) return task;
    el('run-state').textContent = states[task.status];
    el('task-progress').textContent = task.error || stages[task.stage] || task.stage;
    el('cancel-reading').disabled = terminal.has(task.status);
    if (task.result?.answer) {
      originalResult = task.result;
      renderAnswer(task.result.answer, task.result.citations);
      const u = task.result.usage || {};
      el('reading-metrics').textContent = `${task.result.elapsed_seconds || 0} 秒 · ${u.model_calls || 0} 次模型调用 · 输入 ${u.prompt_tokens || 0} / 输出 ${u.completion_tokens || 0} tokens · 缓存 ${task.result.cache_hits || 0} 段 · 上下文 ${task.result.history_turns || 0} 轮`;
      if(task.payload?.use_personal) el('reading-metrics').textContent+=` · 个人材料 ${task.result.personal_sources?.length || 0} 段`;
      renderReview(task.result.evidence_review);
      if (task.kind === 'reading') {
        if (watchedContext === contextEpoch && task.payload?.profile?.revision === profileRevision) {
          readingSession = task.session_id;
        }
        await loadNotes(taskId, token);
        window.LitNotebook?.refresh();
      }
    }
    if (token !== watchToken) return task;
    if (terminal.has(task.status)) {
      source?.close(); source = null; clearInterval(taskTimer); taskTimer = null;
      await loadPapers(); await refreshSessionList();
    }
    await loadTasks();
    return task;
  }
  async function watch(taskId, {restoreSelection=true, activateNotes=true}={}) {
    resetReadingOutput();
    const token = watchToken;
    watchedContext = contextEpoch;
    currentTask = taskId; localStorage.setItem('litagent-last-task', taskId);
    const task = await request(`/api/tasks/${taskId}`);
    if (token !== watchToken) return;
    // Adopt a task's scope before rendering, without treating restoration as a user switch.
    if (restoreSelection && task.kind === 'reading' && watchedContext === contextEpoch) {
      adoptingTask = true;
      try {
        if (skillSpecs[task.payload.skill_id]) selectSkill(task.payload.skill_id);
        setSelectedPapers(task.payload.paper_ids || []);
      } finally { adoptingTask = false; }
      watchedContext = contextEpoch;
    }
    await finishTask(taskId, token, task);
    if (token === watchToken && task.kind === 'reading' && activateNotes) showReadingView('notes');
    if (token !== watchToken || terminal.has(task.status)) return;
    el('cancel-reading').disabled = false;
    let draft = '';
    source = new EventSource(`/api/tasks/${taskId}/events`);
    source.addEventListener('stage', e => {
      if (token !== watchToken) return;
      el('run-state').textContent = states.running;
      el('task-progress').textContent = stages[JSON.parse(e.data).stage] || JSON.parse(e.data).stage;
    });
    source.addEventListener('progress', e => {
      if (token !== watchToken) return;
      const p=JSON.parse(e.data); el('task-progress').textContent=`分段阅读 ${p.completed}/${p.total}`;
    });
    source.addEventListener('delta', e => {
      if (token !== watchToken) return;
      draft += JSON.parse(e.data).text;
      el('run-state').textContent = states.running;
      renderAnswer(draft);
    });
    for (const event of ['done','terminal']) source.addEventListener(event, () => finishTask(taskId, token).catch(report));
    source.addEventListener('error', e => { if(e.data) finishTask(taskId, token).catch(report); });
    taskTimer = setInterval(() => {
      request(`/api/tasks/${taskId}`).then(t => {
        if(token === watchToken && terminal.has(t.status)) finishTask(taskId, token, t).catch(report);
      }).catch(report);
    }, 2500);
  }
  async function startReading() {
    const ids = selectedPapers();
    const spec = skillSpecs[skill] || {min_papers:1, max_papers:1, name:'当前 Skill'};
    if (ids.length < spec.min_papers || ids.length > spec.max_papers) {
      throw new Error(`“${spec.name}”需要选择 ${spec.min_papers === spec.max_papers ? spec.min_papers : `${spec.min_papers}-${spec.max_papers}`} 篇论文。`);
    }
    if (new Set(ids).size !== ids.length) throw new Error('请为论文 A 和论文 B 选择不同论文。');
    resetReadingOutput();
    const token = watchToken, epoch = contextEpoch;
    const task = await request('/api/chat/runs', {method:'POST', body:JSON.stringify({
      message:el('reading-message').value, paper_ids:ids, skill_id:skill, action, session_id:readingSession,
      verify_evidence:el('verify-evidence').checked, use_cache:el('use-cache').checked,
      use_personal:el('use-personal').checked,
      template_id:el('reading-template').value || null,
      template_version:Number(el('reading-template').dataset.version) || null,
    })});
    if (token === watchToken && epoch === contextEpoch) await watch(task.id);
    else await loadTasks();
  }
  async function sendChat() {
    const text = el('chat-input').value.trim(); if(!text) return;
    el('send-btn').disabled = true; el('chat-run-status').textContent = '等待执行';
    try {
      const task = await request('/api/chat/runs', {method:'POST', body:JSON.stringify({message:text, action:'chat', session_id:_currentSessionId})});
      _currentSessionId = task.session_id; el('chat-input').value = '';
      localStorage.setItem('litagent-chat-task', task.id);
      await waitChat(task.id);
    } catch(e) { report(e); el('chat-run-status').textContent = e.message; }
    finally { el('send-btn').disabled = false; }
  }
  async function waitChat(id) {
    for (;;) {
      const task = await request(`/api/tasks/${id}`);
      el('chat-run-status').textContent = task.error || `${states[task.status]} · ${stages[task.stage] || task.stage}`;
      if (terminal.has(task.status)) {
        await refreshSessionList(); await loadSession(task.session_id); el('history-dd').value = task.session_id;
        if(task.error) el('chat-run-status').textContent = task.error;
        localStorage.removeItem('litagent-chat-task'); return;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  el('skill-control').addEventListener('click', e => {
    if (e.target.dataset.skill) selectSkill(e.target.dataset.skill);
  });
  el('reading-template').addEventListener('change', () => { readingSession=null; contextEpoch+=1; });
  el('use-personal').addEventListener('change', () => { readingSession=null; contextEpoch+=1; });
  document.addEventListener('lit-personal-change', () => { readingSession=null; contextEpoch+=1; });
  bind('open-saved-note', () => window.LitNotebook.open(noteTask));
  el('action-control').addEventListener('click', e => {
    if(!e.target.dataset.action) return; action=e.target.dataset.action;
    for (const b of el('action-control').children) b.classList.toggle('selected',b===e.target);
    if(action === 'question') el('reading-message').value = '';
    else if(!el('reading-message').value) {
      lastDefault = skillSpecs[skill]?.default_request || lastDefault;
      el('reading-message').value = lastDefault;
    }
  });
  bind('run-reading', startReading);
  bind('new-reading-session', () => {
    readingSession = null; contextEpoch += 1;
    resetReadingOutput();
    toast('已新建阅读对话','success');
  });
  bind('refresh-reading', async () => {
    await loadPapers(); await loadTasks();
    if (previewPaper) window.LitPDF.show('reading-pdf-preview', previewPaper, '', true);
  });
  bind('process-paper', async () => {
    const ids=selectedPapers(); if(!ids.length) throw new Error('请先选择论文。');
    resetReadingOutput();
    const selectedScope = scope;
    for (const id of ids) {
      const task=await request(`/api/papers/${id}/process`,{method:'POST',body:JSON.stringify({force:el('force-process').checked})});
      if (scope === selectedScope) await watch(task.id);
    }
  });
  bind('cancel-reading', async () => {
    const taskId = currentTask, token = watchToken;
    if (taskId) {
      await request(`/api/tasks/${taskId}/cancel`,{method:'POST'});
      await finishTask(taskId, token);
    }
  });
  bind('save-profile', async () => {
    const body={}; for(const key of profileFields) body[key]=el(`profile-${key}`).value;
    await request('/api/profile',{method:'PUT',body:JSON.stringify(body)});
    readingSession = null; contextEpoch += 1; await loadProfile();
  });
  bind('reset-profile', async () => {
    await request('/api/profile/reset',{method:'POST'});
    readingSession = null; contextEpoch += 1; el('profile-preset').value = ''; await loadProfile();
  });
  bind('save-note', async () => {
    if (!noteTask) return;
    const taskId = noteTask, token = watchToken;
    await request(`/api/tasks/${taskId}/notes`,{method:'PUT',body:JSON.stringify({body:el('note-body').value,feedback:el('note-feedback').value,approved:el('note-approved').checked,expected_version:noteVersions[0]?.version})});
    if (token === watchToken) await loadNotes(taskId, token);
    window.LitNotebook?.refresh(); toast('已保存新版本','success');
  });
  bind('close-evidence', () => { el('evidence-dialog').close(); el('evidence-frame').src='about:blank'; });
  el('note-versions').addEventListener('change', displayNote);
  el('pdf-upload').addEventListener('change', async e => {
    const file=e.target.files[0]; if(!file) return;
    el('upload-status').textContent='上传中…';
    try {
      const body=new FormData(); body.append('file',file);
      const response=await fetch('/api/papers/upload',{method:'POST',body}); const data=await response.json();
      if(!response.ok) throw new Error(data.detail || '上传失败');
      await loadPapers();
      const previous = selectedPapers();
      const selected = skill === 'compare' && previous.length
        ? (previous.includes(data.paper_id) ? previous : [previous[0],data.paper_id]) : [data.paper_id];
      setSelectedPapers(selected);
      el('upload-status').textContent='已入库'; await watch(data.task.id);
    } catch(error) { report(error); el('upload-status').textContent=error.message; }
    finally { e.target.value=''; }
  });
  window.LitReading={
    sendChat,
    templateContext:() => ({paperIds:selectedPapers(),skillId:skill}),
  };
  async function initReading() {
    window.LitPDF.show('reading-pdf-preview', null);
    icons();
    const health=await request('/api/health');
    el('model-status').textContent=`${health.model} · ${health.api_configured ? 'API 已配置' : 'API 未配置'}`;
    el('model-status').dataset.configured = String(health.api_configured);
    await Promise.all([loadSkills(),loadPapers(),loadProfile(),loadPresets(),loadTasks(),window.LitNotebook?.ready]);
    const last=localStorage.getItem('litagent-last-task'); if(last) await watch(last).catch(() => localStorage.removeItem('litagent-last-task'));
    const chat=localStorage.getItem('litagent-chat-task'); if(chat) waitChat(chat).catch(report);
  }
  initReading().catch(report);
})();
