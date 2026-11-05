'use strict';
(() => {
  const $=id=>document.getElementById(id);
  let current=null;
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
  function inlineText(parent,text) {
    text.split(/<br\s*\/?\s*>/i).forEach((part,i)=>{if(i)parent.append(make('br'));parent.append(document.createTextNode(part));});
  }
  // Extracted tables are data, not executable markup. Copy only inert table tags
  // and numeric row/column spans; discard every URL, style and event attribute.
  function safeTable(raw) {
    const inert=raw.replace(/<[^>]*>/g,tag=>{
      const match=tag.match(/^<(\/?)(table|thead|tbody|tfoot|tr|th|td|caption|br|strong|em|b|i|p)\b[^>]*>$/i);
      if(!match)return tag.replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const spans=[...tag.matchAll(/\b(rowspan|colspan)\s*=\s*["']?(\d{1,3})\b/gi)]
        .map(m=>` ${m[1].toLowerCase()}="${Math.max(1,Math.min(Number(m[2]),100))}"`).join('');
      return `<${match[1]}${match[2].toLowerCase()}${match[1]?'':spans}>`;
    });
    const parsed=new DOMParser().parseFromString(inert,'text/html');
    const table=parsed.querySelector('table');
    if(!table)return make('pre',raw);
    const allowed=new Set(['TABLE','THEAD','TBODY','TFOOT','TR','TH','TD','CAPTION','BR','STRONG','EM','B','I','P']);
    function copy(node) {
      if(node.nodeType===3)return document.createTextNode(node.textContent);
      if(node.nodeType!==1)return document.createTextNode('');
      if(!allowed.has(node.tagName))return document.createTextNode(node.textContent);
      const clean=make(node.tagName.toLowerCase());
      for(const attr of ['rowspan','colspan']) {
        const value=node.getAttribute(attr);
        if(value && /^\d{1,3}$/.test(value) && Number(value)>0)clean.setAttribute(attr,String(Math.min(Number(value),100)));
      }
      for(const child of node.childNodes)clean.append(copy(child));
      return clean;
    }
    const wrap=make('div');wrap.className='reader-table';wrap.append(copy(table));return wrap;
  }
  function markdownText(parent,raw) {
    const lines=raw.split('\n');
    for(let i=0;i<lines.length;i++) {
      const line=lines[i],trim=line.trim();
      if(!trim)continue;
      if(trim.startsWith('|')) {
        const table=make('table');
        while(i<lines.length && lines[i].trim().startsWith('|')) {
          const cells=lines[i].trim().replace(/^\||\|$/g,'').split('|');
          if(!cells.every(c=>/^\s*:?-+:?\s*$/.test(c))) {
            const tr=make('tr');for(const cell of cells){const td=make('td');inlineText(td,cell.trim());tr.append(td);}table.append(tr);
          }
          i++;
        }
        i--;const wrap=make('div');wrap.className='reader-table';wrap.append(table);parent.append(wrap);continue;
      }
      const clean=trim.replace(/^#{1,6}\s*/,''),article=clean.match(/^(제\s*\d+\s*조(?:\s*의\s*\d+)?\s*\([^)]*\))(.*)$/);
      if(article){parent.append(make('h3',article[1]));if(article[2].trim())parent.append(make('p',article[2].trim()));}
      else if(/^#{1,6}\s/.test(trim) || /^부\s*칙(?:\s|<|$)/.test(trim))parent.append(make('h3',clean));
      else if(/^!\[.*\]\(/.test(trim)){const note=make('p','이미지 자료 — 원본 파일에서 확인: '+trim);note.className='reader-image-note';parent.append(note);}
      else {const p=make('p');inlineText(p,line);parent.append(p);}
    }
  }
  function render() {
    const kind=$('reader-kind').value,q=$('reader-query').value.trim().toLocaleLowerCase();
    const sections=current.document.sections.filter(s=>(kind==='all'||s.kind===kind)&&(!q||s.text.toLocaleLowerCase().includes(q)));
    $('reader-toc').replaceChildren();$('reader-body').replaceChildren();
    $('reader-status').textContent=`${sections.length}개 구간 표시 / 전체 ${current.document.sections.length}개${q?' · 검색 결과':''}`;
    for(const s of sections) {
      const button=make('button',s.label);button.type='button';button.dataset.section=s.id;$('reader-toc').append(button);
      const section=make('section');section.id='reader-'+s.id;
      const parts=s.text.split(/(<table\b[^>]*>[\s\S]*?<\/table>)/gi);
      for(const part of parts){if(/^<table\b/i.test(part))section.append(safeTable(part));else markdownText(section,part);}
      $('reader-body').append(section);
    }
    if(!sections.length)$('reader-body').append(make('p','일치하는 내용이 없습니다. 검색어나 구분을 변경하세요.'));
    $('reader-body').scrollTop=0;$('reader-toc').scrollTop=0;
  }
  function open(source,article) {
    const regulation=window.REGULATION_MAP?.regulations.find(r=>r.id===source);
    if(!regulation?.document)return;
    current=regulation;$('reader-title').textContent=source;$('reader-meta').textContent=`${regulation.category} · ${regulation.revision} · 저장소 수록 판본`;
    $('reader-kind').value='all';$('reader-query').value='';render();
    if(!$('regulation-reader').open)$('regulation-reader').showModal();
    if(article){const section=regulation.document.sections.find(s=>s.kind==='main' && s.label.replace(/\s/g,'').startsWith(article.replace(/\s/g,'')+'('));if(section)$('reader-'+section.id)?.scrollIntoView({block:'start'});}
  }
  document.addEventListener('click',e=>{const button=e.target.closest('[data-read-source]');if(button)open(button.dataset.readSource,button.dataset.readArticle);});
  $('reader-close').addEventListener('click',()=>$('regulation-reader').close());
  $('reader-query').addEventListener('input',render);$('reader-kind').addEventListener('change',render);
  $('reader-toc').addEventListener('click',e=>{const button=e.target.closest('[data-section]');if(button)$('reader-'+button.dataset.section)?.scrollIntoView({block:'start'});});
  $('reader-download').addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([current.document.sections.map(s=>s.text).join('')],{type:'text/markdown;charset=utf-8'}));
    const a=make('a');a.href=url;a.download=current.id.replace(/[\\/:*?"<>|]/g,'_')+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
})();
