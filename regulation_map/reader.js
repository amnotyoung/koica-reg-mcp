'use strict';
(() => {
  const $=id=>document.getElementById(id);
  let current=null;
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
  // Inline formatting left by the HWP→Markdown conversion. Only paired **bold**
  // with real content becomes <strong>; a lone ** or masked 010-****-**** is a
  // footnote mark or data and stays as written. Backslash escapes are unescaped.
  const INLINE=/(?<!\*)\*\*(?=[^\s*])([^\n]*?[^\s*])\*\*(?!\*)|<(u|sup|sub)>([\s\S]*?)<\/\2>|<br\s*\/?\s*>|\\([\\`*_{}\[\]()#+\-.!|<>~$])/gi;
  function inlineText(parent,text) {
    let last=0;
    for(const m of text.matchAll(INLINE)) {
      if(m.index>last)parent.append(document.createTextNode(text.slice(last,m.index)));
      if(m[1]!==undefined){const strong=make('strong');inlineText(strong,m[1]);parent.append(strong);}
      else if(m[2]){const tag=make(m[2].toLowerCase());inlineText(tag,m[3]);parent.append(tag);}
      else if(m[4]!==undefined)parent.append(document.createTextNode(m[4]));
      else parent.append(make('br'));
      last=m.index+m[0].length;
    }
    if(last<text.length)parent.append(document.createTextNode(text.slice(last)));
  }
  // Extracted tables are data, not executable markup. Copy only inert table tags
  // and numeric row/column spans; discard every URL, style and event attribute.
  function safeTable(raw) {
    const inert=raw.replace(/<[^>]*>/g,tag=>{
      const match=tag.match(/^<(\/?)(table|thead|tbody|tfoot|tr|th|td|caption|br|strong|em|b|i|p|u|sup|sub)\b[^>]*>$/i);
      if(!match)return tag.replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const spans=[...tag.matchAll(/\b(rowspan|colspan)\s*=\s*["']?(\d{1,3})\b/gi)]
        .map(m=>` ${m[1].toLowerCase()}="${Math.max(1,Math.min(Number(m[2]),100))}"`).join('');
      return `<${match[1]}${match[2].toLowerCase()}${match[1]?'':spans}>`;
    });
    const parsed=new DOMParser().parseFromString(inert,'text/html');
    const table=parsed.querySelector('table');
    if(!table)return make('pre',raw);
    const allowed=new Set(['TABLE','THEAD','TBODY','TFOOT','TR','TH','TD','CAPTION','BR','STRONG','EM','B','I','P','U','SUP','SUB']);
    function copy(node) {
      if(node.nodeType===3){const text=document.createDocumentFragment();inlineText(text,node.textContent);return text;}
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
  // On narrow screens the table of contents folds behind a button so the text
  // keeps most of the screen; picking an entry folds it again.
  const narrow=()=>window.matchMedia('(max-width:720px)').matches;
  function setToc(open) {$('reader-layout').classList.toggle('toc-open',open);$('reader-toc-toggle').setAttribute('aria-expanded',String(open));}
  // Official ALIO publication for the extracted text. Only alio.go.kr URLs that
  // the build recorded are linked; nothing is guessed when a value is missing.
  const ALIO=/^https:\/\/www\.alio\.go\.kr\//;
  function renderOfficial(regulation) {
    const o=regulation.official||{},box=$('reader-official');box.replaceChildren();
    const link=(href,label)=>{const a=make('a',label);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;};
    const page=ALIO.test(o.page_url||'')?o.page_url:'',list=ALIO.test(o.list_url||'')?o.list_url:'',file=ALIO.test(o.file_url||'')?o.file_url:'';
    box.append(make('strong','공식 원문 확인'));
    if(page)box.append(link(page,'ALIO 공식 게시 페이지 ↗'));
    else if(list)box.append(link(list,'ALIO KOICA 규정 목록 ↗'));
    if(file)box.append(link(file,'공식 원본 파일 받기 ↗'));
    if(!page && !list && !file)box.append(make('span','링크 정보 없음 · ALIO에서 규정명으로 확인하세요'));
    box.append(make('span',`판본 ${o.revision||regulation.revision} · 저장소 수집 ${o.collected_at||'기록 없음'}`));
  }
  function open(source,article) {
    const regulation=window.REGULATION_MAP?.regulations.find(r=>r.id===source);
    if(!regulation?.document)return;
    current=regulation;$('reader-title').textContent=source;$('reader-meta').textContent=`${regulation.category} · ${regulation.revision} · 저장소 수록 판본`;
    renderOfficial(regulation);
    $('reader-kind').value='all';$('reader-query').value='';setToc(false);render();
    if(!$('regulation-reader').open)$('regulation-reader').showModal();
    if(article){const section=regulation.document.sections.find(s=>s.kind==='main' && s.label.replace(/\s/g,'').startsWith(article.replace(/\s/g,'')+'('));if(section)$('reader-'+section.id)?.scrollIntoView({block:'start'});}
  }
  document.addEventListener('click',e=>{const button=e.target.closest('[data-read-source]');if(button)open(button.dataset.readSource,button.dataset.readArticle);});
  $('reader-close').addEventListener('click',()=>$('regulation-reader').close());
  $('reader-query').addEventListener('input',render);$('reader-kind').addEventListener('change',render);
  $('reader-toc').addEventListener('click',e=>{const button=e.target.closest('[data-section]');if(!button)return;if(narrow())setToc(false);$('reader-'+button.dataset.section)?.scrollIntoView({block:'start'});});
  $('reader-toc-toggle').addEventListener('click',()=>setToc(!$('reader-layout').classList.contains('toc-open')));
  $('reader-download').addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([current.document.sections.map(s=>s.text).join('')],{type:'text/markdown;charset=utf-8'}));
    const a=make('a');a.href=url;a.download=current.id.replace(/[\\/:*?"<>|]/g,'_')+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
})();
