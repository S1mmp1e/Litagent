/* Persistent notes, source-aware reading, and versioned personal templates. */
(() => {
  const el = id => document.getElementById(id);
  const icons = () => window.lucide?.createIcons();
  const report = error => toast(error.message || String(error), 'error');
  const bind = (id, fn) => el(id).addEventListener('click', () => Promise.resolve().then(fn).catch(report));
  const date = stamp => new Date(stamp * 1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  let items = [], current = null, selectedVersion = null, offset = 0, total = 0, paperFilter = null;
  let listSequence = 0, detailSequence = 0, noteDirty = false, templateDirty = false;
  let templates = [], selectedTemplate = null, skillNames = {}, queryTimer;
  let templateRetryId = null, displayedTemplateVersion = null;
  let templateFields = [], templateValues = {}, fieldSequence = 0, templateLoadPhase = 'ready';

  async function request(url, options={}) {
    const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
    return data;
  }
  function approveDiscard(kind) {
    const dirty = kind === 'template' ? templateDirty : noteDirty;
    return !dirty || confirm('有尚未保存的修改，确认放弃这些修改？');
  }
  function showNoteView(view) {
    el('notebook-surface').dataset.view = view;
    for (const button of document.querySelectorAll('[data-note-view]'))
      button.classList.toggle('selected', button.dataset.noteView === view);
  }
  function setEditing(editing) {
    el('notebook-edit-form').hidden = !editing;
    el('notebook-body').hidden = editing;
    el('notebook-versions').disabled = editing;
    el('notebook-edit').disabled = editing || !!current?.trashed_at;
    if (editing) {
      el('notebook-editor').value = selectedVersion.body;
      el('notebook-feedback').value = '';
      // Editing is not approval for training.
      el('notebook-approved').checked = false;
      showNoteView('note');
    }
    noteDirty = false;
  }
  function displayVersion() {
    selectedVersion = current?.versions.find(v => v.version === Number(el('notebook-versions').value));
    if (!selectedVersion) return;
    window.LitMarkdown.renderInto(el('notebook-body'), selectedVersion.body);
    el('notebook-body').dataset.personalTaskId=current.task_id;
    el('notebook-personal').checked=current.retrieval_version===selectedVersion.version;
    el('notebook-personal').disabled=!!current.trashed_at || selectedVersion.version!==current.versions[0].version;
    const base = `/api/notes/${current.task_id}/export?version=${selectedVersion.version}`;
    el('notebook-download').href = base;
    el('notebook-bundle').href = base + '&format=zip';
  }
  function showSource(page=1) {
    const source = current?.sources.find(s => s.document_id === Number(el('notebook-source').value));
    if (source) window.LitPDF.showDocument('notebook-pdf', source, page);
    else window.LitPDF.show('notebook-pdf', null);
  }
  function renderDetail() {
    el('notebook-empty').hidden = !!current;
    el('notebook-detail').hidden = !current;
    if (!current) return;
    el('notebook-detail').dataset.taskId = current.task_id;
    el('notebook-title').value = current.title;
    el('notebook-star').setAttribute('aria-pressed', String(!!current.starred));
    el('notebook-trash').hidden = !!current.trashed_at;
    el('notebook-restore').hidden = !current.trashed_at;
    el('notebook-title').disabled = !!current.trashed_at;
    el('notebook-save-title').disabled = !!current.trashed_at;
    el('notebook-versions').replaceChildren(...current.versions.map(v =>
      new Option(`v${v.version} · ${v.version === 1 ? 'AI 原稿' : '人工修订'} · ${date(v.created_at)}`, v.version)));
    let meta = `${skillNames[current.skill_id] || current.skill_id} · ${date(current.updated_at)}`;
    if (current.template) meta += ` · ${current.template.name} v${current.template.version}`;
    if (current.template_check?.status === 'partial') meta += ` · ${current.template_check.missing_sections.length} 个模板章节未完整匹配`;
    el('notebook-meta').textContent = meta;
    el('notebook-show-template').hidden = !current.template;
    el('notebook-papers').replaceChildren();
    el('notebook-source').replaceChildren();
    current.sources.forEach((source, index) => {
      const name = `${current.sources.length > 1 ? `${index === 0 ? 'A' : 'B'} · ` : ''}${source.title}`;
      el('notebook-source').add(new Option(name, source.document_id));
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = name + (source.in_library ? '' : ' · 原文存档');
      button.addEventListener('click', () => {
        el('notebook-source').value = source.document_id;
        el('notebook-evidence').hidden = true;
        showNoteView('pdf'); showSource();
      });
      el('notebook-papers').append(button);
    });
    el('notebook-evidence').hidden = true;
    setEditing(false); displayVersion(); showSource(); icons();
  }
  function renderList() {
    const list = el('notebook-list');
    list.replaceChildren();
    for (const item of items) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'notebook-item';
      button.dataset.taskId = item.task_id;
      button.classList.toggle('selected', item.task_id === current?.task_id);
      const heading = document.createElement('strong');
      heading.textContent = `${item.starred ? '★ ' : ''}${item.title}`;
      const paper = document.createElement('span');
      paper.textContent = item.sources.map(s => s.title).join(' / ');
      const meta = document.createElement('span'); meta.className = 'notebook-item-meta';
      meta.textContent = `${skillNames[item.skill_id] || item.skill_id} · v${item.version} · ${date(item.updated_at)}`;
      button.append(heading,paper,meta);
      button.addEventListener('click', () => openNote(item.task_id).catch(report));
      list.append(button);
    }
    if (!items.length) {
      const empty = document.createElement('p'); empty.className = 'status-label';
      empty.textContent = el('notebook-filter').value === 'trash' ? '回收站为空' : '没有符合条件的笔记';
      list.append(empty);
    }
    el('notebook-count').textContent = `${total} 篇笔记`;
    el('notebook-page').textContent = total ? `${offset+1}–${Math.min(offset+items.length,total)} / ${total}` : '0 / 0';
    el('notebook-prev').disabled = offset === 0;
    el('notebook-next').disabled = offset + items.length >= total;
    el('notebook-clear-paper').hidden = paperFilter === null;
  }
  async function loadList() {
    const token = ++listSequence;
    const params = new URLSearchParams({query:el('notebook-search').value, view:el('notebook-filter').value,
      offset:String(offset), limit:'30'});
    if (paperFilter !== null) params.set('paper_id', paperFilter);
    if (el('notebook-category').value) params.set('category_id', el('notebook-category').value);
    if (el('notebook-skill').value) params.set('skill_id', el('notebook-skill').value);
    const data = await request('/api/notes?' + params);
    if (token !== listSequence) return;
    items = data.items; total = data.total;
    if (!items.length && offset > 0) { offset = Math.max(0,offset-30); return loadList(); }
    renderList();
  }
  async function openNote(taskId) {
    if (!approveDiscard('note')) return;
    const token = ++detailSequence;
    el('notebook-detail').inert = true;
    el('notebook-detail').setAttribute('aria-busy','true');
    try {
      const note = await request(`/api/notes/${encodeURIComponent(taskId)}`);
      if (token !== detailSequence) return;
      current = note; noteDirty = false;
      el('tab6').checked = true;
      history.replaceState(null,'',`#note=${encodeURIComponent(taskId)}`);
      renderDetail(); renderList(); showNoteView('note');
      if (matchMedia('(max-width:700px)').matches) el('notebook-detail').scrollIntoView({block:'start'});
    } finally {
      if (token === detailSequence) {
        el('notebook-detail').inert = false;
        el('notebook-detail').setAttribute('aria-busy','false');
      }
    }
  }
  async function openPaper(paperId) {
    if (!approveDiscard('note')) return;
    detailSequence++; current = null; noteDirty = false;
    el('notebook-detail').inert = false;
    paperFilter = paperId; offset = 0;
    el('notebook-filter').value = 'all'; el('notebook-search').value = '';
    el('notebook-category').value = ''; el('notebook-skill').value = '';
    el('tab6').checked = true; el('notebook-layout').classList.remove('list-collapsed');
    history.replaceState(null,'',location.pathname + location.search);
    renderDetail(); await loadList();
  }
  async function updateMetadata(values) {
    if (!current) return;
    const id = current.task_id;
    const updated = await request(`/api/notes/${id}`, {method:'PATCH',body:JSON.stringify(values)});
    if (current?.task_id === id) { current = updated; renderDetail(); }
    await loadList(); refreshCounts();
  }
  el('notebook-personal').addEventListener('change',async()=>{
    if(!current||!selectedVersion)return;
    const id=current.task_id, version=selectedVersion.version, enabled=el('notebook-personal').checked;
    el('notebook-personal').disabled=true;
    try{
      await request(`/api/personal/notes/${id}`,{method:'PUT',body:JSON.stringify({version,enabled})});
      if(current?.task_id===id){current.retrieval_version=enabled ? version : null;displayVersion();}
      document.dispatchEvent(new CustomEvent('lit-personal-change'));
    }catch(error){if(current?.task_id===id)displayVersion();report(error);}
  });
  async function refreshCounts() {
    try {
      const counts = await request('/api/notes/paper-counts');
      _paperNoteCounts = Object.fromEntries(counts.map(item => [item.paper_id,item.count]));
      renderLibTable(_currentLibRows);
    } catch (error) { report(error); }
  }
  async function loadFilters() {
    const [categories,skills] = await Promise.all([request('/api/categories'),request('/api/skills')]);
    const previous = el('notebook-category').value;
    el('notebook-category').replaceChildren(new Option('全部分类夹',''),
      ...categories.map(c => new Option(c.name,c.id)));
    el('notebook-category').value = previous;
    skillNames = Object.fromEntries(skills.map(s => [s.id,s.name]));
    if (el('notebook-skill').options.length === 1)
      skills.forEach(s => el('notebook-skill').add(new Option(s.name,s.id)));
  }

  async function loadTemplates(preferred) {
    templates = await request('/api/note-templates');
    const select = el('reading-template');
    const value = preferred ?? select.value;
    select.replaceChildren(new Option('跟随阅读模式',''), ...templates.map(t =>
      new Option(`${t.name}${t.builtin ? ' · 内置' : ''}`,t.id)));
    select.value = templates.some(t => t.id === value) ? value : '';
    select.dataset.version = templates.find(t => t.id === select.value)?.version || '';
    el('template-list').replaceChildren();
    for (const template of templates) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'template-choice'; button.dataset.templateId = template.id;
      button.classList.toggle('selected', template.id === selectedTemplate?.id);
      const title = document.createElement('strong'), info = document.createElement('span');
      title.textContent = template.name;
      info.textContent = `${template.builtin ? '内置模板' : '个人模板'} · v${template.version}`;
      button.append(title,info);
      button.addEventListener('click', () => selectTemplate(template.id).catch(report));
      el('template-list').append(button);
    }
  }
  async function loadTemplateFields() {
    const token = ++fieldSequence;
    const context = window.LitReading?.templateContext() || {paperIds:[],skillId:'overview'};
    const params = new URLSearchParams({skill_id:context.skillId});
    context.paperIds.forEach(id => params.append('paper_ids',id));
    templateFields = []; templateValues = {};
    const menu = el('template-variable');
    menu.replaceChildren(new Option('正在加载字段…',''));
    menu.disabled = true;
    previewTemplate();
    const data = await request('/api/note-template-fields?' + params);
    if (token !== fieldSequence) return;
    templateFields = data.fields; templateValues = data.values;
    menu.replaceChildren(new Option('插入动态字段',''));
    const groups = new Map();
    for (const field of templateFields) {
      if (!groups.has(field.group)) {
        const group = document.createElement('optgroup');
        group.label = field.group; groups.set(field.group,group); menu.append(group);
      }
      groups.get(field.group).append(new Option(field.label,field.id));
    }
    menu.disabled = templateLoadPhase !== 'ready';
    previewTemplate();
  }
  function previewTemplate() {
    const text = el('template-body').value.replace(/\{\{([a-z][a-z0-9_]*)\}\}/g,
      (match,key) => Object.hasOwn(templateValues,key) ? templateValues[key] : match);
    window.LitMarkdown.renderInto(el('template-preview'), text);
  }
  function templatePhase(phase) {
    templateLoadPhase = phase;
    const unavailable = phase !== 'ready';
    el('template-editor').setAttribute('aria-busy', String(phase === 'loading'));
    for (const id of ['template-name','template-description','template-body','template-variable','template-copy'])
      el(id).disabled = unavailable;
    el('template-variable').disabled = unavailable || !templateFields.length;
    el('template-body').placeholder = phase === 'loading' ? '正在加载模板正文…' : '';
    el('template-delete').disabled = unavailable || !selectedTemplate || !!selectedTemplate.builtin;
    el('template-save').disabled = unavailable || !!selectedTemplate?.builtin;
    el('template-use').disabled = unavailable || !selectedTemplate;
    const hasHistory = (selectedTemplate?.versions?.length || 0) > 1;
    el('template-history').hidden = !hasHistory;
    el('template-versions').disabled = unavailable || !hasHistory;
    el('template-retry').hidden = phase !== 'error';
    el('template-state').textContent = phase === 'loading' ? '正在加载模板正文…' :
      phase === 'error' ? '模板加载失败，请重试' :
      selectedTemplate?.builtin ? '内置模板' :
      selectedTemplate ? `当前 v${selectedTemplate.version}` : '草稿 · 未保存';
  }
  function displayTemplate(value) {
    displayedTemplateVersion = value.version || null;
    el('template-name').value = value.name || '';
    el('template-description').value = value.description || '';
    el('template-body').value = value.body || '';
    el('template-body').setSelectionRange(0,0);
    el('template-body').scrollTop = 0;
    el('template-body').scrollLeft = 0;
    el('template-insert-status').hidden = true;
    el('template-insert-status').textContent = '';
    el('template-variable').value = '';
    templatePhase('ready');
    templateDirty = false; previewTemplate();
    el('template-preview').scrollTop = 0;
    for (const button of el('template-list').children)
      button.classList.toggle('selected', button.dataset.templateId === selectedTemplate?.id);
  }
  let templateSequence = 0;
  async function selectTemplate(id) {
    if (!approveDiscard('template')) return;
    const token = ++templateSequence;
    templateRetryId = id;
    // The catalog already includes the body; show it while loading version history.
    selectedTemplate = templates.find(t => t.id === id) || null;
    el('template-versions').replaceChildren();
    displayTemplate(selectedTemplate || {});
    templatePhase('loading');
    try {
      const value = await request(`/api/note-templates/${encodeURIComponent(id)}`);
      if (token !== templateSequence) return;
      selectedTemplate = value;
      el('template-versions').replaceChildren(...value.versions.map(v =>
        new Option(`v${v.version}${v.version === value.version ? ' · 最新' : ''}`,v.version)));
      displayTemplate(value);
    } catch (error) {
      if (token !== templateSequence) return;
      templatePhase('error');
      throw error;
    }
  }
  async function openTemplates() {
    if (el('templates-dialog').open) return;
    const token = ++templateSequence;
    templateRetryId = null;
    selectedTemplate = templates.find(t => t.id === el('reading-template').value) || templates[0] || null;
    el('template-versions').replaceChildren();
    displayTemplate(selectedTemplate || {});
    templatePhase('loading');
    el('templates-dialog').showModal();
    try {
      await Promise.all([loadTemplates(),loadTemplateFields()]);
      if (token !== templateSequence) return;
      const preferred = el('reading-template').value || templates[0]?.id;
      if (preferred) await selectTemplate(preferred);
      else newTemplate();
    } catch (error) {
      if (token === templateSequence) templatePhase('error');
      throw error;
    }
    icons();
  }
  function newTemplate() {
    if (!approveDiscard('template')) return;
    templateSequence++; selectedTemplate = null; templateRetryId = null;
    el('template-versions').replaceChildren();
    displayTemplate({});
    el('template-name').focus();
  }
  async function saveTemplate(copy=false) {
    const token = templateSequence;
    const body = {name:el('template-name').value,description:el('template-description').value,body:el('template-body').value};
    const updating = selectedTemplate && !copy;
    if (updating) body.expected_version = selectedTemplate.version;
    const saved = await request(updating ? `/api/note-templates/${selectedTemplate.id}` : '/api/note-templates',
      {method:updating ? 'PUT' : 'POST',body:JSON.stringify(body)});
    const previousReading = el('reading-template').value;
    if (token === templateSequence) templateDirty = false;
    await loadTemplates(previousReading);
    if (token === templateSequence) await selectTemplate(saved.id);
    if (previousReading === saved.id) el('reading-template').dispatchEvent(new Event('change'));
    toast(`模板已保存 · v${saved.version}`,'success');
    return saved;
  }

  for (const id of ['notebook-editor','notebook-feedback','notebook-approved'])
    el(id).addEventListener('input', () => { noteDirty = true; });
  el('notebook-versions').addEventListener('change', displayVersion);
  el('notebook-source').addEventListener('change', () => { el('notebook-evidence').hidden = true; showSource(); });
  for (const button of document.querySelectorAll('[data-note-view]'))
    button.addEventListener('click', () => showNoteView(button.dataset.noteView));
  el('notebook-body').addEventListener('click', event => {
    const button = event.target.closest('[data-citation]');
    if (!button) return;
    event.stopPropagation();
    const citation = current?.citations.find(c => c.id === button.dataset.citation);
    if (!citation) { report(new Error('该引用未保留原文快照。')); return; }
    el('notebook-source').value = citation.document_id;
    el('notebook-evidence-label').textContent = `PDF 第 ${citation.page} 页`;
    el('notebook-evidence-text').textContent = citation.text;
    el('notebook-evidence').hidden = false;
    showNoteView('pdf'); showSource(citation.page);
    if (matchMedia('(max-width:700px)').matches)
      el('notebook-source').scrollIntoView({block:'start',behavior:'instant'});
  });
  el('tab6').addEventListener('change', () => {
    if (el('tab6').checked) Promise.all([loadFilters(),loadList()]).catch(report);
  });
  for (const radio of document.querySelectorAll('.tab-radio:not(#tab6)'))
    radio.addEventListener('change', () => {
      if (radio.checked && location.hash.startsWith('#note='))
        history.replaceState(null,'',location.pathname + location.search);
    });
  el('notebook-search').addEventListener('input', () => {
    clearTimeout(queryTimer);
    queryTimer = setTimeout(() => { offset=0; loadList().catch(report); },250);
  });
  for (const id of ['notebook-category','notebook-skill','notebook-filter'])
    el(id).addEventListener('change', () => { offset=0; loadList().catch(report); });
  bind('notebook-refresh', async () => { await loadFilters(); await loadList(); });
  bind('notebook-prev', () => { offset=Math.max(0,offset-30); return loadList(); });
  bind('notebook-next', () => { offset+=30; return loadList(); });
  bind('notebook-clear-paper', () => { paperFilter=null; offset=0; return loadList(); });
  bind('notebook-collapse', () => el('notebook-layout').classList.add('list-collapsed'));
  bind('notebook-expand', () => el('notebook-layout').classList.remove('list-collapsed'));
  bind('notebook-edit', () => setEditing(true));
  bind('notebook-cancel-edit', () => { if (approveDiscard('note')) setEditing(false); });
  bind('notebook-save-title', async () => {
    if (approveDiscard('note')) { await updateMetadata({title:el('notebook-title').value}); toast('标题已保存','success'); }
  });
  bind('notebook-star', () => {
    if (approveDiscard('note')) return updateMetadata({starred:!current.starred});
  });
  bind('notebook-trash', async () => {
    if (!approveDiscard('note')) return;
    await updateMetadata({trashed:true}); toast('已移入回收站','success');
  });
  bind('notebook-restore', () => updateMetadata({trashed:false}));
  bind('notebook-save', async () => {
    const id = current.task_id;
    el('notebook-save').disabled = true;
    try {
      await request(`/api/tasks/${id}/notes`, {method:'PUT',body:JSON.stringify({
        body:el('notebook-editor').value, feedback:el('notebook-feedback').value,
        approved:el('notebook-approved').checked, expected_version:current.versions[0].version,
      })});
      noteDirty=false; await openNote(id); await loadList(); toast('已保存新版本','success');
    } finally { el('notebook-save').disabled=false; }
  });
  bind('notebook-show-template', () => {
    el('template-snapshot-title').textContent = `${current.template.name} · v${current.template.version}`;
    window.LitMarkdown.renderInto(el('template-snapshot-body'),current.template.body);
    el('template-snapshot-dialog').showModal();
  });
  bind('template-snapshot-close', () => el('template-snapshot-dialog').close());
  bind('open-note-templates', openTemplates); bind('notebook-templates', openTemplates);
  bind('template-retry', async () => {
    if (templateRetryId && templateFields.length) return selectTemplate(templateRetryId);
    const token = ++templateSequence;
    templatePhase('loading');
    try {
      await Promise.all([loadTemplates(),loadTemplateFields()]);
      if (token !== templateSequence) return;
      const preferred = el('reading-template').value || templates[0]?.id;
      if (preferred) await selectTemplate(preferred);
      else newTemplate();
    } catch (error) {
      if (token === templateSequence) templatePhase('error');
      throw error;
    }
  });
  bind('template-new', newTemplate);
  bind('template-save', () => saveTemplate());
  bind('template-copy', () => saveTemplate(true));
  bind('templates-close', () => { if (approveDiscard('template')) { templateDirty=false; el('templates-dialog').close(); } });
  el('templates-dialog').addEventListener('cancel', event => {
    if (!approveDiscard('template')) event.preventDefault(); else templateDirty=false;
  });
  el('templates-dialog').addEventListener('close', () => { templateSequence++; fieldSequence++; });
  bind('template-delete', async () => {
    if (!confirm('删除这个个人模板？已有笔记和已提交任务会保留原模板版本。')) return;
    await request(`/api/note-templates/${selectedTemplate.id}`,{method:'DELETE'});
    templateDirty=false; selectedTemplate=null; await loadTemplates();
    el('reading-template').dispatchEvent(new Event('change'));
    await selectTemplate(templates[0].id);
  });
  bind('template-use', () => {
    if (templateDirty) throw new Error('请先保存模板修改；内置模板修改后请另存为个人模板。');
    el('reading-template').value=selectedTemplate.id;
    el('reading-template').dispatchEvent(new Event('change'));
    el('templates-dialog').close(); el('tab5').checked=true;
    el('tab5').dispatchEvent(new Event('change'));
    toast(`已选择 ${selectedTemplate.name}`,'success');
  });
  for (const id of ['template-name','template-description','template-body'])
    el(id).addEventListener('input', () => {
      templateDirty=true;
      el('template-state').textContent='未保存';
      el('template-insert-status').hidden=true;
      previewTemplate();
    });
  el('template-variable').addEventListener('change', () => {
    const field=el('template-variable').value;
    const name=templateFields.find(item => item.id===field)?.label;
    if (!name) return;
    const input=el('template-body');
    const text=`{{${field}}}`;
    el('template-variable').value='';
    if (input.value.length - (input.selectionEnd-input.selectionStart) + text.length > input.maxLength) {
      report(new Error(`模板正文最多 ${input.maxLength} 字，无法继续插入字段。`));
      return;
    }
    const start=input.selectionStart;
    // Focus the collapsed caret first so the browser reveals an off-screen insertion.
    input.setRangeText(text,start,input.selectionEnd,'end');
    input.dispatchEvent(new Event('input'));
    input.focus();
    input.setSelectionRange(start,start+text.length);
    el('template-insert-status').textContent=`已插入：${name}`;
    el('template-insert-status').hidden=false;
  });
  el('template-versions').addEventListener('change', () => {
    const value=selectedTemplate?.versions?.find(v => v.version===Number(el('template-versions').value));
    if (!value || !approveDiscard('template')) {
      el('template-versions').value=displayedTemplateVersion || '';
      return;
    }
    displayTemplate(value);
    if (value.version !== selectedTemplate.version) {
      templateDirty=true; el('template-state').textContent=`历史 v${value.version} · 保存将创建新版本`;
    }
  });
  el('reading-template').addEventListener('change', () => {
    el('reading-template').dataset.version=templates.find(t => t.id===el('reading-template').value)?.version || '';
  });
  window.addEventListener('beforeunload', event => {
    if (noteDirty || templateDirty) { event.preventDefault(); event.returnValue=''; }
  });
  const ready=Promise.all([loadFilters(),loadTemplates(),loadList()]).then(async () => {
    const match=location.hash.match(/^#note=([a-f0-9]{32})$/);
    if (match) await openNote(match[1]);
  }).catch(report);
  window.LitNotebook = {
    ready, open:openNote,
    openPaper:id => openPaper(id).catch(report),
    refresh:() => { loadList().catch(report); refreshCounts(); },
  };
  icons();
})();
