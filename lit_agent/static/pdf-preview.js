/* Shared local-PDF viewer for reading, library, and category selections. */
(() => {
  const instances = new Map();
  const pendingDownloads = new Map();
  let library;
  function pdfLibrary() {
    library ||= import('/static/vendor/pdfjs/pdf.min.mjs').then(pdfjs => {
      pdfjs.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.mjs';
      return pdfjs;
    }).catch(error => { library = null; throw error; });
    return library;
  }
  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`);
    return data;
  }
  function download(paperId) {
    // Multiple panels selecting the same paper share one in-flight download.
    if (!pendingDownloads.has(paperId)) {
      const pending = request(`/api/papers/${paperId}/preview`, {method:'POST'})
        .finally(() => pendingDownloads.delete(paperId));
      pendingDownloads.set(paperId, pending);
    }
    return pendingDownloads.get(paperId);
  }
  function create(root) {
    root.classList.add('pdf-preview');
    root.innerHTML = `
      <div class="pdf-toolbar">
        <div class="pdf-caption"><strong class="pdf-title">PDF 原文</strong><span class="pdf-meta"></span></div>
        <div class="pdf-actions">
          <button type="button" class="btn icon-btn pdf-retry" title="重新加载 PDF" aria-label="重新加载 PDF" disabled><i data-lucide="refresh-cw"></i></button>
          <a class="btn icon-btn pdf-open" title="在新窗口打开 PDF" aria-label="在新窗口打开 PDF" target="_blank" rel="noopener noreferrer" hidden><i data-lucide="external-link"></i></a>
        </div>
      </div>
      <div class="pdf-status" role="status">尚未选择论文</div>
      <div class="pdf-reader" hidden>
        <div class="pdf-controls">
          <div class="pdf-page-controls">
            <button class="btn icon-btn pdf-prev" title="上一页" aria-label="上一页"><i data-lucide="chevron-left"></i></button>
            <input class="pdf-page-number" type="number" min="1" value="1" aria-label="PDF 页码">
            <span class="pdf-page-count"></span>
            <button class="btn icon-btn pdf-next" title="下一页" aria-label="下一页"><i data-lucide="chevron-right"></i></button>
          </div>
          <div class="pdf-zoom-controls">
            <button class="btn icon-btn pdf-minus" title="缩小" aria-label="缩小"><i data-lucide="zoom-out"></i></button>
            <button class="btn icon-btn pdf-fit" title="适应宽度" aria-label="适应宽度"><i data-lucide="move-horizontal"></i></button>
            <button class="btn icon-btn pdf-plus" title="放大" aria-label="放大"><i data-lucide="zoom-in"></i></button>
          </div>
        </div>
        <div class="pdf-viewport" tabindex="0" aria-label="PDF 页面"><div class="pdf-page"></div></div>
      </div>`;
    const title = root.querySelector('.pdf-title'), meta = root.querySelector('.pdf-meta');
    const status = root.querySelector('.pdf-status'), reader = root.querySelector('.pdf-reader');
    const viewport = root.querySelector('.pdf-viewport'), pageSurface = root.querySelector('.pdf-page');
    const pageInput = root.querySelector('.pdf-page-number'), count = root.querySelector('.pdf-page-count');
    const prev = root.querySelector('.pdf-prev'), next = root.querySelector('.pdf-next');
    const minus = root.querySelector('.pdf-minus'), plus = root.querySelector('.pdf-plus');
    const open = root.querySelector('.pdf-open'), retry = root.querySelector('.pdf-retry');
    const annotations = window.LitAnnotations.attach(root, goToPage);
    let selected = null, selectedPaper = null, directSource = null, pendingPage = 1;
    let sequence = 0, controller = null, resizeTimer = null;
    let loadingTask = null, pdf = null, pdfjs = null, renderTask = null, textTask = null;
    let cleanup = Promise.resolve();
    let pageNumber = 1, zoom = 1, renderSequence = 0, renderedWidth = 0;
    function message(text, state) {
      status.textContent = text;
      status.hidden = !text;
      root.dataset.state = state;
    }
    function link(url) {
      open.hidden = !url;
      if (url) open.href = url;
      else open.removeAttribute('href');
    }
    function controls() {
      pageInput.value = pageNumber;
      pageInput.max = pdf?.numPages || 1;
      count.textContent = `/ ${pdf?.numPages || 0}`;
      prev.disabled = !pdf || pageNumber <= 1;
      next.disabled = !pdf || pageNumber >= pdf.numPages;
      minus.disabled = zoom <= 0.5;
      plus.disabled = zoom >= 2;
    }
    async function render(resetScroll = false) {
      if (!pdf || viewport.clientWidth <= 0) return;
      const token = ++renderSequence, documentToken = sequence;
      renderTask?.cancel();
      textTask?.cancel();
      controls();
      renderedWidth = viewport.clientWidth;
      try {
        const page = await pdf.getPage(pageNumber);
        if (token !== renderSequence || documentToken !== sequence) return;
        const base = page.getViewport({scale:1});
        const style = getComputedStyle(viewport);
        const width = Math.max(100, viewport.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
        const view = page.getViewport({scale:width / base.width * zoom});
        const canvas = document.createElement('canvas'), text = document.createElement('div');
        // A new canvas per render prevents old/cancelled renders overwriting the selected page.
        const density = Math.min(devicePixelRatio || 1, 2, Math.sqrt(16000000 / (view.width * view.height)));
        canvas.width = Math.floor(view.width * density);
        canvas.height = Math.floor(view.height * density);
        canvas.style.width = `${view.width}px`;
        canvas.style.height = `${view.height}px`;
        canvas.setAttribute('role','img');
        canvas.setAttribute('aria-label',`${title.textContent}，第 ${pageNumber} 页`);
        text.className = 'textLayer';
        pageSurface.style.width = `${view.width}px`;
        pageSurface.style.height = `${view.height}px`;
        pageSurface.style.setProperty('--scale-factor', view.scale);
        pageSurface.style.setProperty('--total-scale-factor', view.scale);
        pageSurface.replaceChildren(canvas, text);
        renderTask = page.render({canvasContext:canvas.getContext('2d', {alpha:false}), viewport:view,
          transform:density === 1 ? null : [density,0,0,density,0,0]});
        await renderTask.promise;
        if (token !== renderSequence || documentToken !== sequence) return;
        textTask = new pdfjs.TextLayer({textContentSource:page.streamTextContent(), container:text, viewport:view});
        await textTask.render();
        if (token !== renderSequence || documentToken !== sequence) return;
        root.dataset.page = String(pageNumber);
        annotations.render(pageNumber, view);
        message('', 'ready');
        if (resetScroll) viewport.scrollTo(0,0);
      } catch (error) {
        if (token === renderSequence && documentToken === sequence && error.name !== 'RenderingCancelledException') {
          message('此页渲染失败，可重试或在新窗口打开 PDF。', 'error');
        }
      }
    }
    async function show(paperId, label = '', force = false, documentInfo = null, requestedPage = 1) {
      const key = documentInfo ? documentInfo.pdf_url : paperId;
      pendingPage = requestedPage;
      if (key === selected && !force) {
        if (documentInfo && pdf) goToPage(requestedPage);
        return;
      }
      selected = key; selectedPaper = paperId; directSource = documentInfo;
      const token = ++sequence;
      annotations.reset();
      renderSequence++;
      controller?.abort();
      clearTimeout(resizeTimer);
      renderTask?.cancel();
      textTask?.cancel();
      if (loadingTask) {
        const previous = loadingTask;
        // Finish the load handshake before tearing down a superseded PDF worker.
        cleanup = cleanup.then(async () => {
          await previous.promise.catch(() => {});
          await previous.destroy();
        }).catch(() => {});
      }
      loadingTask = pdf = null;
      pageNumber = zoom = 1;
      root.dataset.paperId = paperId || '';
      root.dataset.documentId = documentInfo?.document_id || '';
      delete root.dataset.page;
      controller = new AbortController();
      reader.hidden = true;
      pageSurface.replaceChildren();
      link(null);
      title.textContent = label || 'PDF 原文';
      meta.textContent = '';
      retry.disabled = !paperId;
      if (!paperId) { message('尚未选择论文', 'empty'); return; }
      message('正在查找 PDF…', 'loading');
      try {
        let info = documentInfo || await request(`/api/papers/${paperId}/preview`, {signal:controller.signal});
        if (token !== sequence) return;
        title.textContent = info.title;
        link(info.source_url);
        if (!info.pdf_url && info.can_download) {
          message('正在下载 PDF…', 'loading');
          info = await download(paperId);
          if (token !== sequence) return;
        }
        if (!info.pdf_url) {
          message(info.source_url ? '暂无本地 PDF，可打开原文链接。' : '暂无 PDF 文件，请先导入 PDF。', 'empty');
          return;
        }
        link(info.pdf_url);
        root.dataset.documentId = info.document_id || '';
        annotations.source(info);
        message('正在打开 PDF…', 'loading');
        pdfjs = await pdfLibrary();
        await cleanup;
        if (token !== sequence) return;
        loadingTask = pdfjs.getDocument({url:info.pdf_url, isEvalSupported:false,
          cMapUrl:'/static/vendor/pdfjs/cmaps/', cMapPacked:true,
          standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/', wasmUrl:'/static/vendor/pdfjs/wasm/'});
        const document = await loadingTask.promise;
        if (token !== sequence) return;
        pdf = document;
        pageNumber = Math.min(pdf.numPages, Math.max(1, Number(pendingPage) || 1));
        meta.textContent = `${pdf.numPages} 页 · 本地 PDF`;
        controls();
        reader.hidden = false;
        await render(true);
      } catch (error) {
        if (token === sequence && error.name !== 'AbortError') {
          message(error.message || 'PDF 加载失败，请重试。', 'error');
        }
      }
    }
    function goToPage(number) {
      if (!pdf) return;
      const requested = Number(number);
      if (!Number.isInteger(requested) || requested < 1 || requested > pdf.numPages) {
        pageInput.value = pageNumber; return;
      }
      pageNumber = requested; render(true);
    }
    prev.addEventListener('click', () => goToPage(pageNumber - 1));
    next.addEventListener('click', () => goToPage(pageNumber + 1));
    pageInput.addEventListener('change', () => goToPage(pageInput.value));
    pageInput.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); goToPage(pageInput.value); } });
    for (const [button, step] of [[minus,-0.25],[plus,0.25],[root.querySelector('.pdf-fit'),0]]) {
      button.addEventListener('click', () => { zoom = step ? Math.max(0.5, Math.min(2, zoom + step)) : 1; render(); });
    }
    new ResizeObserver(() => {
      if (!pdf || viewport.clientWidth <= 0 || viewport.clientWidth === renderedWidth) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => render(), 80);
    }).observe(viewport);
    retry.addEventListener('click', () => show(selectedPaper, title.textContent, true, directSource, pageNumber));
    return {show};
  }
  window.LitPDF = {
    show(id, paperId, title, force = false) {
      if (!instances.has(id)) instances.set(id, create(document.getElementById(id)));
      instances.get(id).show(paperId, title, force);
      window.lucide?.createIcons();
    },
    showDocument(id, info, page = 1) {
      if (!instances.has(id)) instances.set(id, create(document.getElementById(id)));
      instances.get(id).show(info.paper_id, info.title, false, info, page);
      window.lucide?.createIcons();
    },
  };
})();
