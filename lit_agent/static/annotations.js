/* PDF-coordinate anchors remain tied to the exact file across zoom and reindexing. */
(() => {
  const icons = () => window.lucide?.createIcons();
  const report = error => toast(error.message || String(error), 'error');
  async function request(url, options={}) {
    const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '批注请求失败');
    return data;
  }
  function button(icon, label, action) {
    const node = document.createElement('button');
    node.type = 'button'; node.className = 'btn icon-btn'; node.title = node.ariaLabel = label;
    node.innerHTML = `<i data-lucide="${icon}"></i>`;
    node.addEventListener('click', () => Promise.resolve().then(action).catch(report));
    return node;
  }
  function attach(root, goToPage) {
    const reader = root.querySelector('.pdf-reader'), surface = root.querySelector('.pdf-page');
    let info = null, page = 1, view = null, entries = [], selection = null, editing = null;
    let epoch = 0, listEpoch = 0, focusId = null;
    const toggle = button('highlighter', '批注列表', () => {
      panel.hidden = !panel.hidden; toggle.setAttribute('aria-expanded', String(!panel.hidden));
    });
    toggle.classList.add('pdf-annotations-toggle'); toggle.setAttribute('aria-expanded','false');
    root.querySelector('.pdf-zoom-controls').append(toggle);
    const selectionBar = document.createElement('div');
    selectionBar.className = 'annotation-selection'; selectionBar.hidden = true;
    const excerpt = document.createElement('span');
    const add = button('message-square-plus', '添加批注', () => edit(null, selection));
    add.addEventListener('mousedown', event => event.preventDefault());
    selectionBar.append(excerpt, add);
    reader.insertBefore(selectionBar, root.querySelector('.pdf-viewport'));
    const panel = document.createElement('section');
    panel.className = 'annotations-panel'; panel.hidden = true;
    panel.setAttribute('aria-label','PDF 批注');
    panel.innerHTML = `
      <div class="annotations-heading"><strong>我的批注 <span class="annotation-count"></span></strong></div>
      <p class="annotation-status" role="status"></p>
      <form class="annotation-editor" hidden>
        <div class="annotation-version-row"><strong class="annotation-editor-title">新增批注</strong><select class="annotation-versions" aria-label="批注历史版本" hidden></select></div>
        <blockquote class="annotation-quote"></blockquote>
        <label class="field-label">个人评论<textarea class="annotation-comment" aria-label="个人评论" maxlength="6000" rows="3"></textarea></label>
        <div class="annotation-options"><div class="annotation-colors" role="group" aria-label="高亮颜色"></div>
          <label class="check-label"><input type="checkbox" class="annotation-enabled">用于后续问答</label></div>
        <div class="annotation-editor-actions"><button class="btn primary annotation-save" type="submit"><i data-lucide="save"></i>保存批注</button><button class="btn annotation-cancel" type="button">取消</button></div>
      </form>
      <div class="annotation-list"></div>`;
    reader.append(panel);
    const list = panel.querySelector('.annotation-list'), status = panel.querySelector('.annotation-status');
    const form = panel.querySelector('form'), comment = panel.querySelector('.annotation-comment');
    const quote = panel.querySelector('.annotation-quote'), enabled = panel.querySelector('.annotation-enabled');
    const versions = panel.querySelector('.annotation-versions');
    const save = panel.querySelector('.annotation-save');
    const colors = panel.querySelector('.annotation-colors');
    for (const [color, label] of [['yellow','黄色'],['green','绿色'],['pink','粉色']]) {
      const swatch = document.createElement('label');
      swatch.className = `annotation-swatch ${color}`; swatch.title = label;
      const input = document.createElement('input');
      input.type='radio'; input.name=`${root.id}-annotation-color`; input.value=color; input.ariaLabel=label;
      swatch.append(input); colors.append(swatch);
    }
    function setColor(color) { colors.querySelector(`input[value="${color}"]`).checked = true; }
    function closeEditor() { form.hidden=true; editing=null; selection=null; selectionBar.hidden=true; }
    function changed() {
      document.dispatchEvent(new CustomEvent('lit-personal-change', {detail:{origin:root.id,paperId:info?.paper_id}}));
    }
    function draw() {
      surface.querySelector('.annotation-layer')?.remove();
      if (!view) return;
      const layer = document.createElement('div'); layer.className='annotation-layer';
      for (const entry of entries.filter(a=>a.page===page)) {
        for (const rect of entry.rects) {
          const [x1,y1,x2,y2] = view.convertToViewportRectangle(rect);
          const mark = document.createElement('span');
          mark.className=`annotation-highlight ${entry.color}${entry.id===focusId ? ' focused' : ''}`;
          mark.dataset.annotationId=entry.id;
          Object.assign(mark.style,{left:`${Math.min(x1,x2)}px`,top:`${Math.min(y1,y2)}px`,
            width:`${Math.abs(x2-x1)}px`,height:`${Math.abs(y2-y1)}px`});
          layer.append(mark);
        }
      }
      surface.append(layer);
    }
    function renderList() {
      panel.querySelector('.annotation-count').textContent=`(${entries.length})`;
      list.replaceChildren();
      if (!entries.length) {
        const empty=document.createElement('p'); empty.className='status-label'; empty.textContent='暂无批注'; list.append(empty);
      }
      for (const entry of entries) {
        const item=document.createElement('article'); item.className=`annotation-item ${entry.color}`;
        item.dataset.annotationId=entry.id;
        const header=document.createElement('div'); header.className='annotation-item-heading';
        const label=document.createElement('span');
        label.textContent=`第 ${entry.page} 页 · v${entry.revision}${entry.enabled ? ' · 已用于问答' : ''}`;
        const locate=button('crosshair','定位批注',()=>{
          focusId=entry.id; goToPage(entry.page);
          root.querySelector('.pdf-viewport').scrollIntoView({block:'nearest'});
        });
        const editButton=button('square-pen','编辑批注',()=>edit(entry));
        const remove=button('trash-2','删除批注',async()=>{
          if (!confirm('删除这条批注？')) return;
          const token=epoch;
          await request(`/api/annotations/${entry.id}?expected_revision=${entry.revision}`,{method:'DELETE'});
          if(token!==epoch)return;
          if(editing?.id===entry.id)closeEditor();
          changed(); await load();
        });
        header.append(label,locate,editButton,remove);
        const text=document.createElement('blockquote'); text.textContent=entry.quote;
        const body=document.createElement('p'); body.textContent=entry.comment;
        item.append(header,text,body);
        if(entry.anchor_status==='unmatched'){
          const warning=document.createElement('span'); warning.className='annotation-warning';
          warning.textContent='摘录未与解析原文匹配'; item.append(warning);
        }
        list.append(item);
      }
      draw(); icons();
    }
    async function load() {
      const token=epoch, serial=++listEpoch;
      if(!info?.file_hash)return;
      status.textContent='正在加载批注…';
      try {
        const rows=await request(`/api/annotations?paper_id=${info.paper_id}&file_hash=${info.file_hash}`);
        if(token!==epoch||serial!==listEpoch)return;
        entries=rows; status.textContent=''; renderList();
      }catch(error){
        if(token!==epoch||serial!==listEpoch)return;
        status.textContent=error.message;
      }
    }
    async function edit(entry, picked=null) {
      if(!entry&&!picked)return;
      const token=epoch;
      editing=entry ? {...entry} : {...picked};
      panel.hidden=false; toggle.setAttribute('aria-expanded','true'); form.hidden=false;
      selectionBar.hidden=true; versions.hidden=true; versions.replaceChildren();
      quote.textContent=editing.quote; comment.value=entry?.comment || ''; enabled.checked=entry ? entry.enabled : true;
      setColor(entry?.color || 'yellow'); save.disabled=false;
      panel.querySelector('.annotation-editor-title').textContent=entry ? '编辑批注' : '新增批注';
      comment.focus(); form.scrollIntoView({block:'nearest'});
      if(entry){
        const detail=await request(`/api/annotations/${entry.id}`);
        if(token!==epoch||editing?.id!==entry.id)return;
        if(detail.revision!==entry.revision){
          status.textContent='批注已更新，请重新打开。'; save.disabled=true; await load(); return;
        }
        versions.replaceChildren(...detail.versions.map(v=>new Option(`v${v.revision}`,v.revision)));
        versions.hidden=detail.versions.length<2;
        versions.onchange=()=>{
          const old=detail.versions.find(v=>v.revision===Number(versions.value));
          comment.value=old.comment; enabled.checked=old.enabled; setColor(old.color);
        };
      }
    }
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      if(!editing||!info||save.disabled)return;
      const token=epoch, draft=editing, source={...info};
      const body={comment:comment.value,color:colors.querySelector('input:checked').value,enabled:enabled.checked};
      save.disabled=true; status.textContent='正在保存…';
      try{
        const value=draft.id
          ? await request(`/api/annotations/${draft.id}`,{method:'PUT',body:JSON.stringify({...body,expected_revision:draft.revision})})
          : await request('/api/annotations',{method:'POST',body:JSON.stringify({...draft,...body,
              paper_id:source.paper_id,file_hash:source.file_hash})});
        if(token!==epoch)return;
        focusId=value.id; closeEditor(); changed(); await load(); status.textContent='已保存';
      }catch(error){if(token===epoch)status.textContent=error.message;}
      finally{if(token===epoch)save.disabled=false;}
    });
    panel.querySelector('.annotation-cancel').addEventListener('click',closeEditor);
    function captureSelection() {
      if(!info?.file_hash||!view)return;
      const selected=window.getSelection();
      if(!selected?.rangeCount||selected.isCollapsed){selectionBar.hidden=true;return;}
      const range=selected.getRangeAt(0), textLayer=surface.querySelector('.textLayer');
      if(!textLayer?.contains(range.startContainer)||!textLayer.contains(range.endContainer))return;
      const text=selected.toString().trim();
      if(!text||text.length>6000)return;
      const box=surface.getBoundingClientRect();
      const rects=Array.from(range.getClientRects()).filter(r=>r.width>1&&r.height>1).map(r=>{
        const a=view.convertToPdfPoint(Math.max(0,r.left-box.left),Math.max(0,r.top-box.top));
        const b=view.convertToPdfPoint(Math.min(view.width,r.right-box.left),Math.min(view.height,r.bottom-box.top));
        return [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[0],b[0]),Math.max(a[1],b[1])];
      }).filter(r=>r[2]>r[0]&&r[3]>r[1]);
      if(!rects.length||rects.length>200)return;
      const before=range.cloneRange(), after=range.cloneRange();
      before.selectNodeContents(textLayer);before.setEnd(range.startContainer,range.startOffset);
      after.selectNodeContents(textLayer);after.setStart(range.endContainer,range.endOffset);
      selection={page,quote:text,rects,prefix:before.toString().slice(-200),suffix:after.toString().slice(0,200)};
      excerpt.textContent=text; selectionBar.hidden=false;
    }
    surface.addEventListener('mouseup',captureSelection);
    surface.addEventListener('keyup',captureSelection);
    surface.addEventListener('touchend',()=>setTimeout(captureSelection,50));
    document.addEventListener('lit-personal-change',event=>{
      if(event.detail?.origin!==root.id&&event.detail?.paperId===info?.paper_id)load();
    });
    return {
      reset(){
        epoch++; listEpoch++; info=null; view=null; entries=[]; focusId=null; closeEditor();
        panel.hidden=true; toggle.setAttribute('aria-expanded','false'); status.textContent=''; list.replaceChildren();
      },
      source(value){info=value;load();},
      render(number,viewport){page=number;view=viewport;selectionBar.hidden=true;draw();},
    };
  }
  const dialog=document.createElement('dialog'); dialog.className='personal-evidence-dialog';
  dialog.innerHTML='<div class="result-heading"><h3>个人材料引用</h3></div><p class="personal-evidence-meta"></p><div class="personal-evidence-text"></div><div class="personal-evidence-actions"></div>';
  dialog.querySelector('.result-heading').append(button('x','关闭个人材料',()=>dialog.close()));
  document.body.append(dialog);
  let evidenceEpoch=0;
  document.addEventListener('click',async event=>{
    const target=event.target.closest('[data-personal-citation]');
    if(!target)return;
    const taskId=target.closest('[data-personal-task-id]')?.dataset.personalTaskId;
    if(!taskId)return;
    const token=++evidenceEpoch;
    try{
      const source=await request(`/api/personal/evidence/${taskId}/${target.dataset.personalCitation}`);
      if(token!==evidenceEpoch)return;
      dialog.querySelector('h3').textContent=source.kind==='annotation'?'个人批注':'个人笔记';
      dialog.querySelector('.personal-evidence-meta').textContent=`${source.title} · v${source.version}${source.page ? ` · 第 ${source.page} 页` : ''} · ${source.current ? '生成时使用的材料' : '历史快照，来源已修改或停用'}`;
      dialog.querySelector('.personal-evidence-text').textContent=source.text;
      const actions=dialog.querySelector('.personal-evidence-actions');actions.replaceChildren();
      if(source.kind==='note'){
        const open=button('notebook-pen','打开来源笔记',()=>{dialog.close();return window.LitNotebook.open(source.source_id);});
        actions.append(open);
      }
      if(source.pdf_url){
        const link=document.createElement('a');link.className='btn';
        link.textContent='打开批注原文';link.href=source.pdf_url;link.target='_blank';link.rel='noopener noreferrer';
        actions.append(link);
      }
      if(!dialog.open)dialog.showModal();icons();
    }catch(error){report(error);}
  });
  window.LitAnnotations={attach};
})();
