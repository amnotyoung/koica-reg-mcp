'use strict';

// The workbook maps document titles. Keep these edges independent of article
// citations and authored duty priorities, including when the titles coincide.
window.createLawMap = function(snapshot, regulations, openRegulation, selectLaw) {
  const {laws = {}, mappings = {}, checked} = snapshot || {};
  document.getElementById('open-laws').hidden = !snapshot;
  const $ = id => document.getElementById(id);
  const dialog = $('law-dialog');
  const diagram = $('law-diagram');
  const results = $('law-matches');
  const query = $('law-query');
  const reverse = new Map(Object.keys(laws).map(title => [title, []]));
  for (const [reg, mapping] of Object.entries(mappings)) {
    if (!regulations.has(reg)) continue;
    for (const title of Object.keys(mapping.refs)) reverse.get(title).push(reg);
  }
  const entries = [
    ...Array.from(regulations, ([name, r]) => ({kind:'reg', name, label:'KOICA 내규 · ' + r.category})),
    ...Array.from(reverse, ([name, regs]) => ({kind:'law', name, label:lawLabel(name), regs})).filter(e => e.regs.length),
  ];
  const clean = s => s.normalize('NFC').toLowerCase().replace(/[\s·ㆍ‧]/g, '');
  function search(term) {
    const q = clean(term);
    if (!q) return [];
    const rank = name => name === q ? 0 : name.startsWith(q) ? 1 : 2;
    return entries.filter(e => clean(e.name).includes(q) ||
      (e.kind === 'law' && laws[e.name].aliases?.some(a => clean(a).includes(q))))
      .sort((a,b) => rank(clean(a.name)) - rank(clean(b.name)) || a.name.localeCompare(b.name,'ko'));
  }
  function el(tag, style, text) {
    const node = document.createElement(tag);
    if (style) node.className = style;
    if (text) node.textContent = text;
    return node;
  }
  function button(label, action, style='outline-button') {
    const b = el('button', style, label);
    b.type = 'button';
    b.addEventListener('click', action);
    return b;
  }
  function link(label, href) {
    const a = el('a','law-link', label + ' ↗');
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  }
  function lawUrl(record) {
    const base = 'https://www.law.go.kr/LSW/';
    if (record.lawId) return base + 'lsInfoP.do?lsId=' + encodeURIComponent(record.lawId);
    if (record.adminSeq) return base + 'admRulInfoP.do?admRulSeq=' + encodeURIComponent(record.adminSeq);
    if (record.treatySeq) return base + 'trtyInfoP.do?trtySeq=' + encodeURIComponent(record.treatySeq);
    if (record.historicalMst) return base + 'lsInfoP.do?lsiSeq=' + encodeURIComponent(record.historicalMst);
    return null;
  }
  function lawLabel(name) {
    const record=laws[name];
    if (!lawUrl(record)) return '제명 확인 필요';
    return record.historicalMst ? '폐지 · '+record.type : record.type;
  }
  function lawSources(name) {
    const record=laws[name], href=lawUrl(record);
    const sources=el('div','law-sources');
    sources.append(el('p','law-caption',href ? (record.historicalMst?'폐지 판본 확인':'공식 제명 확인')+' · '+checked : '공식 제명 확인 필요'));
    if (href) sources.append(link(record.historicalMst?'폐지 판본 보기':'국가법령정보센터 원문',href));
    else sources.append(link('국가법령정보센터에서 검색','https://www.law.go.kr/lsSc.do?query='+encodeURIComponent(name)));
    if (record.decreeId) sources.append(link('시행령 원문',lawUrl({lawId:record.decreeId})));
    return sources;
  }
  const count = name => Object.keys(mappings[name]?.refs || {}).length;
  let trail = [];
  function clearSearch() {
    query.value = ''; results.hidden = true; results.replaceChildren();
  }
  function connectionDetails(reg,law) {
    const mapping=mappings[reg], internal=regulations.get(reg);
    const hits=mapping.evidence?.[law] || [], review=mapping.reviewed?.[law];
    const details=el('details','law-evidence');
    details.dataset.law=law;
    const summary=el('summary','',review?'연결 이유 · '+review.article:
      hits.length?'연결 근거 보기':'근거 조문 확인 필요');
    details.append(summary);
    // Build quotations only when opened. Several regulations cite one law many times.
    details.addEventListener('toggle',()=>{
      if (!details.open || details.children.length>1) return;
      if (review) {
        details.append(el('strong','','연결 해설'),el('p','',review.summary),el('p','',review.reference),
          link(review.reference_label+' · '+review.checked+' 확인',review.reference_url));
      }
      if (!hits.length) details.append(el('p','','이 내규의 본칙에서 해당 법령을 직접 인용한 문구는 아직 확인하지 못했습니다.'));
      if (hits.length && hits.every(([, ,refs=[]])=>refs.length===0)) {
        details.append(el('p','law-caption','내규 인용문에서 상위법령의 조문 번호를 확인할 수 없습니다.'));
      }
      for (const [index,lineNo,refs=[]] of hits) {
        const article=internal.articles[index], quote=article.body.split('\n')[lineNo];
        const internalExcerpt=el('section','law-excerpt law-reg');
        internalExcerpt.append(el('span','excerpt-label','KOICA 내규 발췌'),
          el('strong','','「'+reg+'」 '+article.article+' ('+article.title+')'),
          el('blockquote','',quote),el('small','law-caption',internal.revision));
        if (internal.official?.file_url) internalExcerpt.append(link('ALIO 내규 원본',internal.official.file_url));
        details.append(internalExcerpt);
        for (const ref of refs) {
          if (ref.unresolved) {
            details.append(el('p','law-caption',ref.unresolved));
            continue;
          }
          const excerpt=el('section','law-excerpt law-law');
          const label=ref.contextual?'상위법령 발췌 · 참고 조문':ref.text?'상위법령 발췌':'인용된 상위법령';
          excerpt.append(el('span','excerpt-label',label),
            el('strong','','「'+ref.title+'」 '+ref.provision));
          if (ref.sync_review) {
            excerpt.append(el('p','law-caption',ref.sync_review.reason+' · 이전 판본 발췌'),
              link('대조할 판본 · '+ref.sync_review.version,ref.sync_review.url));
          }
          if (ref.text) {
            excerpt.append(el('blockquote','',ref.text));
            if (ref.note) excerpt.append(el('p','law-caption',ref.note));
            excerpt.append(el('small','law-caption',ref.version),
              link('상위법령 원문',ref.url));
          } else excerpt.append(el('small','law-caption','인용 판본의 조문 원문 확인 필요'));
          details.append(excerpt);
        }
      }
      if (!hits.length && internal.official?.file_url) details.append(link('ALIO 내규 원본',internal.official.file_url));
    });
    return details;
  }
  function render(kind, name) {
    const isLaw = kind === 'law';
    const mapping = mappings[name];
    const neighbors = isLaw ? reverse.get(name) : Object.keys(mapping?.refs || {});
    clearSearch();
    const root = el('section', 'law-root law-' + kind);
    const top = el('div','law-root-top');
    top.append(el('span','law-kind',isLaw ? lawLabel(name) : 'KOICA 내규'));
    if (trail.length > 1) top.append(button('← 이전', () => {
      trail.pop(); render(...trail[trail.length-1]);
    }, 'law-back'));
    const heading = el('h3','',name);
    heading.tabIndex = -1;
    root.append(top, heading);
    if (isLaw) {
      root.append(lawSources(name));
    } else {
      root.append(button('내규 상세 보기 ↗', () => { dialog.close(); openRegulation(name); }, 'law-back'));
    }
    const group = el('section','law-neighbors');
    group.append(el('h4','', (isLaw ? '연결된 KOICA 내규' : '관련 법령·행정규칙 등') + ' · ' + neighbors.length + '종'));
    if (neighbors.length) {
      const list = el('ul','law-branches');
      for (const other of neighbors) {
        const reg = isLaw ? other : name, law = isLaw ? name : other;
        const item = el('li');
        const node = button('', () => navigate(isLaw ? 'reg' : 'law',other), 'law-node law-' + (isLaw ? 'reg' : 'law'));
        node.append(el('span','law-kind', isLaw ? 'KOICA 내규' : lawLabel(law)),
          el('strong','',other));
        item.append(node,connectionDetails(reg,law)); list.append(item);
      }
      group.append(list);
    } else {
      group.append(el('p','empty','현재 이 내규에 연결된 외부 법령이 없습니다.'));
    }
    if (!isLaw && mapping?.notes) group.append(el('p','law-caption','추가 참고 · ' + mapping.notes.join(', ')));
    diagram.replaceChildren(root,group);
    dialog.querySelector('.law-scroll').scrollTop = 0;
    heading.focus({preventScroll:true});
  }
  function navigate(kind, name) {
    trail.push([kind,name]);
    render(kind,name);
  }
  function open(kind, name) {
    trail = [];
    if (!dialog.open) dialog.showModal();
    if (name) navigate(kind,name);
    else {
      clearSearch();
      diagram.replaceChildren(el('p','empty','탐색할 내규 또는 법령의 이름을 입력하세요.'));
      query.focus();
    }
  }
  function inspect(name, relatedReg, visibleRegs, visitReg) {
    const header=el('section','law-law');
    header.append(el('h3','',name),el('span','pill',lawLabel(name)));
    const connected=new Set(reverse.get(name));
    const displayOrder=new Set([relatedReg,...visibleRegs]);
    const current=[...displayOrder].filter(reg=>visibleRegs.has(reg) && connected.has(reg));
    const others=[...connected].filter(reg=>!visibleRegs.has(reg));
    const section=el('section','inspector-law-relations');
    function itemFor(reg, expanded) {
      const item=el('div','inspector-law-item');
      const evidence=connectionDetails(reg,name);
      evidence.open=expanded;
      item.append(button(reg,()=>visitReg(reg),'text-button'),evidence);
      return item;
    }
    if (current.length) {
      section.append(el('h4','','현재 지도에서 연결된 내규 · '+current.length+'종'));
      for (const reg of current) section.append(itemFor(reg,true));
    } else section.append(el('p','detail-description','현재 지도에는 이 법령과 연결된 내규가 없습니다.'));
    if (others.length) {
      const more=el('details','more-relations');
      more.append(el('summary','','다른 연결 내규 보기 · '+others.length+'종'));
      for (const reg of others) more.append(itemFor(reg,false));
      section.append(more);
    }
    const sources=el('section','law-law');
    sources.append(lawSources(name));
    $('detail').replaceChildren(header,section,sources);
  }
  query.addEventListener('input', () => {
    const hits = search(query.value);
    results.replaceChildren(...hits.map(e => {
      const b = button('',() => navigate(e.kind,e.name),'law-result');
      b.append(el('strong','',e.name), el('small','',e.label));
      return b;
    }));
    if (!hits.length) results.append(el('p','empty','일치하는 이름이 없습니다.'));
    results.hidden = !query.value.trim();
  });
  query.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' && !results.hidden) {event.preventDefault(); results.querySelector('button')?.focus();}
    if (event.key === 'Enter' && !event.isComposing && !results.hidden) results.querySelector('button')?.click();
  });
  dialog.addEventListener('cancel', event => {
    if (!results.hidden) {event.preventDefault(); clearSearch(); query.focus();}
  });
  $('close-laws').addEventListener('click',() => dialog.close());
  $('law-provenance').textContent = '연결 근거에서 내규의 조문과 인용 문구를 확인할 수 있습니다. 공식 제명을 확인한 법령은 국가법령정보센터 원문으로 연결됩니다. 법령 확인일 '+checked+'.';
  function preview(reg) {
    const panel = $('law-preview');
    const names = Object.keys(mappings[reg]?.refs || {});
    panel.replaceChildren();
    panel.hidden = !names.length;
    if (!names.length) return;
    panel.append(el('span','law-preview-title',reg + ' → 상위법령'));
    for (const name of names.slice(0,3)) {
      const b = button(name,() => selectLaw(name,reg),'law-chip');
      b.title = lawLabel(name);
      panel.append(b);
    }
    panel.append(button('전체 ' + names.length + '종 보기',() => open('reg',reg),'law-preview-all'));
  }
  return {open, inspect, search, count, preview, lawLabel};
};
