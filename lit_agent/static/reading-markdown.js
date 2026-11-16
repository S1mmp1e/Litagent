/* Render untrusted model text before adding application-owned citation controls. */
(() => {
  function renderInto(container, text) {
    const html = marked.parse(String(text || ''), {gfm: true, breaks: true});
    container.innerHTML = DOMPurify.sanitize(html, {
      USE_PROFILES: {html: true},
      FORBID_TAGS: ['img', 'style', 'form', 'input', 'button', 'textarea', 'select'],
      FORBID_ATTR: ['style', 'id', 'name'],
      ALLOW_DATA_ATTR: false,
    });
    container.classList.add('content-md');
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement.closest('pre, code, a') && /\[(?:cite|personal):[a-f0-9-]{36}\]/.test(node.data)) nodes.push(node);
    }
    for (const node of nodes) {
      const fragment = document.createDocumentFragment();
      let offset = 0;
      for (const match of node.data.matchAll(/\[(cite|personal):([a-f0-9-]{36})\]/g)) {
        fragment.append(document.createTextNode(node.data.slice(offset, match.index)));
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'citation-button';
        if(match[1]==='personal'){
          button.dataset.personalCitation=match[2];
          button.classList.add('personal-citation-button');
          button.title='查看个人材料'; button.textContent='[个人笔记]';
        }else{
          button.dataset.citation = match[2];
          button.title = '查看原文证据'; button.textContent = '[原文]';
        }
        fragment.append(button);
        offset = match.index + match[0].length;
      }
      fragment.append(document.createTextNode(node.data.slice(offset)));
      node.replaceWith(fragment);
    }
    for (const link of container.querySelectorAll('a[href]')) {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    }
  }
  window.LitMarkdown = {renderInto};
})();
