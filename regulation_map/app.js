'use strict';
(() => {
  const D = window.REGULATION_MAP;
  const $ = id => document.getElementById(id);
  if (!D) { $('detail').textContent = '데이터가 없습니다. python3 regulation_map/build.py를 먼저 실행해주세요.'; return; }
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const MP = window.MapPriority;
  const priority = id => MP.priority(state.duty.links,id);
  const badge = p => `<span class="priority-badge ${esc(p.level)}">${esc(p.label)}</span>`;
  const regs = new Map(D.regulations.map(r => [r.id, r]));
  const adjacent = id => D.relations.filter(e => e.from === id || e.to === id);
  const state = {department: null, duty: null, focus: null, selected: null, page: 0, list: window.matchMedia('(max-width:720px)').matches, zoom: 1, pan: {x:0,y:0}};
  const color = category => ({'규정':'#507c72','시행세칙':'#82719d','지침':'#4e7b97','기준':'#a98040','정관':'#697c62'}[category] || '#798c7b');
  function saveState() {
    const q = new URLSearchParams({department:state.department.id,duty:state.duty.id});
    if (state.focus) q.set('regulation',state.focus);
    history.replaceState(null,'','#'+q.toString());
  }
  let departmentResults=[], departmentActive=-1, departmentComposing=false;
  function closeDepartmentSearch() {
    $('department-results').hidden=true;
    $('department-search').setAttribute('aria-expanded','false');
    $('department-search').removeAttribute('aria-activedescendant');
    if(state.department) {
      $('department-search').value=state.department.name;
      $('department-search-status').textContent=`${state.department.name} 선택됨`;
    }
  }
  function highlightDepartment(index, scroll=false) {
    departmentActive=index;
    const rows=$('department-results').querySelectorAll('[role="option"]');
    rows.forEach((row,i)=>row.setAttribute('aria-selected',String(i===index)));
    if(index>=0 && rows[index]) {
      $('department-search').setAttribute('aria-activedescendant',rows[index].id);
      if(scroll) rows[index].scrollIntoView({block:'nearest'});
    } else $('department-search').removeAttribute('aria-activedescendant');
  }
  function openDepartmentSearch(query, preferCurrent=false) {
    departmentResults=MP.departmentMatches(D.departments,query);
    $('department-results').innerHTML=departmentResults.length?departmentResults.map((d,i)=>`<div id="department-option-${i}" role="option" aria-selected="false" data-department-index="${i}"><strong>${esc(d.name)}</strong><small>${esc([d.division,...d.path.slice(0,-1)].join(' / '))}</small></div>`).join(''):'<p class="department-empty">일치하는 부서가 없습니다.</p>';
    $('department-results').hidden=false;
    $('department-search').setAttribute('aria-expanded','true');
    $('department-search-status').textContent=departmentResults.length?`${departmentResults.length}개 부서 · ↑↓ 이동, Enter 선택`:'검색 결과가 없습니다. 다른 부서명이나 본부명을 입력하세요.';
    const selected=preferCurrent?departmentResults.findIndex(d=>d.id===state.department?.id):-1;
    highlightDepartment(departmentResults.length?Math.max(0,selected):-1,preferCurrent);
  }
  function commitDepartment(index) {
    const match=departmentResults[index];
    if(match) selectDepartment(match.id);
  }
  function selectDepartment(id, dutyId) {
    const department = D.departments.find(d=>d.id===id);
    if (!department) return;
    state.department=department;
    closeDepartmentSearch();
    state.duty=department.duties.find(d=>d.id===dutyId) || department.duties.find(d=>d.links.length) || department.duties[0];
    state.focus=null;state.selected=null;state.page=0;$('priority-filter').value='all';
    $('department-path').textContent=department.division+' / '+department.path.join(' / ');
    $('duty-count').textContent=department.duties.length;
    renderDuties();resetZoom();render();showDuty();saveState();
  }
  function renderDuties() {
    $('duties').innerHTML=state.department.duties.map(d=>`<button class="duty ${state.duty.id===d.id?'active':''}" data-duty="${esc(d.id)}" aria-pressed="${state.duty.id===d.id}"><span class="duty-number">${esc(d.number.padStart(2,'0'))}</span><span>${esc(d.title)}${d.shared?'<span class="shared">지역부서 공통업무</span>':''}<span class="review-status ${d.links.length?'has-review':''}">${d.review.status==='stale'?'원문 변경 · 재검토 필요':d.links.length?'내용 연결 '+d.links.length+'종':'미검토'}</span></span></button>`).join('');
  }
  function graphData() {
    const filter=$('priority-filter').value;
    if (state.focus) {
      const result=MP.referencePage(D.relations,state.duty.links,state.focus,filter,state.page);
      state.page=result.page;
      return {center:state.focus,...result,edges:MP.visibleReferences(D.relations,[state.focus,...result.nodes],state.selected||state.focus)};
    }
    const ids=state.duty.links.map(r=>r.source),nodes=MP.ordered(state.duty.links,ids,filter);
    const edges=MP.visibleReferences(D.relations,nodes,state.selected);
    if ($('show-reviewed').checked) nodes.forEach(id=>edges.push({from:'@duty',to:id,kind:'reviewed'}));
    return {center:'@duty',nodes,edges,total:ids.length,matched:nodes.length};
  }
  function svg(tag, attrs={}, text) {
    const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
    Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));
    if (text!==undefined) el.textContent=text;
    return el;
  }
  function lines(text,max=15) {
    const parts=[];let current='';
    for (const c of text) {current+=c;if(current.length>=max){parts.push(current);current='';}}
    if(current)parts.push(current);
    return parts.length>3?[parts[0],parts[1],parts.slice(2).join('').slice(0,max-1)+'…']:parts;
  }
  function render() {
    const g=graphData();state.graph=g;
    $('map-title').textContent=state.focus || state.duty.title;
    $('map-subtitle').textContent=state.focus?'이 규정이 참조하는 규정과, 이 규정을 참조하는 규정':`${state.department.name} · ${state.duty.shared?'공통업무':'분장업무'} ${state.duty.number}에서 시작한 규정 지도`;
    $('show-reviewed').disabled=!!state.focus;
    const explicit=g.edges.filter(e=>e.kind==='explicit').length;
    const refSummary=(state.selected||state.focus)?` · 선택 규정 참조 ${explicit}개 표시`:'';
    $('map-summary').textContent=state.focus?`연결 규정 ${g.total}종 · 필터 결과 ${g.matched}종 중 ${g.nodes.length}종 표시${refSummary}`:`내용 검토 ${g.total}종 중 ${g.nodes.length}종 표시 · ${state.duty.review.status==='stale'?'재검토 필요':g.total?'일부 범위 검토':'미검토'}${refSummary}`;
    $('graph-hint').textContent=state.focus?'선택한 규정의 참조만 표시합니다. 다른 연결은 노드를 선택해 확인하세요.':state.selected?'선택한 규정의 참조를 표시합니다. 업무 노드를 선택하면 해제됩니다.':'규정을 선택하면 해당 규정의 참조선과 근거를 볼 수 있습니다.';
    $('neighbor-pager').hidden=!state.focus || g.pages<2;
    $('neighbor-page-label').textContent=state.focus?`${g.page+1} / ${g.pages} 페이지`:'';
    $('previous-neighbors').disabled=!state.focus || g.page===0;
    $('next-neighbors').disabled=!state.focus || g.page+1>=g.pages;
    const first=state.duty.links.find(link=>link.priority.level==='core');
    document.querySelector('.map-toolbar').hidden=state.list;
    document.querySelector('.map-bottom').hidden=state.list;
    $('reference-legend').hidden=!explicit;
    $('work-legend').hidden=!!state.focus;
    $('work-links-control').hidden=!!state.focus;
    const viewport=$('viewport');viewport.replaceChildren();
    let positions,width,height;
    if(!state.focus) {
      const layout=MP.dutyLayout(state.duty.links,g.nodes);
      ({positions,width,height}=layout);
      for(const band of layout.bands) {
        viewport.append(svg('line',{x1:band.x+20,y1:78,x2:band.x+band.width-20,y2:78,class:'column-rule'}));
        viewport.append(svg('text',{x:band.x+20,y:62,class:`band-label ${band.level}`},`${band.label} · ${band.count}종`));
      }
    } else {
      const many=g.nodes.length>8;
      width=many?1400:1100;height=many?900:720;
      positions=new Map([[g.center,{x:width/2,y:height/2,w:236,h:112}]]);
      g.nodes.forEach((id,i)=>{
        const angle=-Math.PI/2+i*2*Math.PI/Math.max(g.nodes.length,1),level=priority(id).level;
        const size=level==='core'?{w:236,h:112}:level==='conditional'?{w:184,h:94}:{w:204,h:98};
        positions.set(id,{x:width/2+Math.cos(angle)*(many?540:395),y:height/2+Math.sin(angle)*(many?350:265),...size});
      });
    }
    state.canvas={width,height};
    $('map-svg').setAttribute('viewBox',`0 0 ${width} ${height}`);
    const connection=(from,to)=>{const a=positions.get(from),b=positions.get(to);const dx=b.x-a.x,dy=b.y-a.y;const ka=1/Math.max(Math.abs(dx)/(a.w/2+5),Math.abs(dy)/(a.h/2+5));const kb=1/Math.max(Math.abs(dx)/(b.w/2+5),Math.abs(dy)/(b.h/2+5));return `M ${a.x+dx*ka} ${a.y+dy*ka} L ${b.x-dx*kb} ${b.y-dy*kb}`;};
    g.edges.forEach(e=>{
      const group=svg('g',{class:'edge-group',role:'button',tabindex:0,'aria-label':e.kind==='explicit'?`${e.from} → ${e.to} 참조 근거`:`${e.to} 업무 내용 연결 근거`});
      let path=connection(e.from,e.to);
      if(e.kind==='explicit' && g.edges.some(other=>other.from===e.to && other.to===e.from)) {
        const a=positions.get(e.from),b=positions.get(e.to),length=Math.hypot(b.x-a.x,b.y-a.y);
        const midX=(a.x+b.x)/2-(b.y-a.y)*28/length,midY=(a.y+b.y)/2+(b.x-a.x)*28/length;
        path=path.replace(' L ',` Q ${midX} ${midY} `);
      }
      group.append(svg('path',{d:path,class:'edge-hit'}));
      group.append(svg('path',{d:path,class:`edge ${e.kind} ${e.kind==='reviewed'?priority(e.to).level:''} ${e.content_review?'content-reference':''} ${state.selected?(e.from===state.selected||e.to===state.selected?'emphasized':'dimmed'):''}`, ...(e.kind==='explicit'?{'marker-end':'url(#arrow)'}:{})}));
      group.append(svg('title',{},e.kind==='explicit'?`${e.from} → ${e.to}\n명시적 참조 · 근거 ${e.evidence.length}건`:(state.duty.links.find(l=>l.source===e.to)?.role || '업무 내용 연결')));
      const activate=()=>e.kind==='explicit'?showEdge(e):selectRegulation(e.to);
      group.addEventListener('click',activate);group.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();activate();}});viewport.append(group);
    });
    for (const [id,p] of positions) {
      const central=id===g.center, duty=id==='@duty', r=regs.get(id);
      const title=duty?state.duty.title:id,prio=duty?null:priority(id);
      const group=svg('g',{class:`node ${central?'central':''} ${duty?'':prio.level} ${state.selected===id?'selected':''} ${!state.focus&&!state.selected&&first?.source===id?'entry':''}`,transform:`translate(${p.x-p.w/2} ${p.y-p.h/2})`,tabindex:0,role:'button','aria-label':duty?'선택한 업무: '+title:'규정: '+title});
      group.append(svg('rect',{width:p.w,height:p.h,rx:11,class:'node-box'}));
      const category=duty?'업무':prio.label;
      if(duty || state.focus) {
        group.append(svg('text',{x:15,y:25,class:'badge',fill:central?'#b7d8c6':color(category)},category));
      }
      const titleLines=lines(title,p.w>=230?17:p.w<200?13:15);
      titleLines.forEach((l,i)=>group.append(svg('text',{x:15,y:(duty||state.focus?50:34)+i*17,class:'title'},l)));
      if(titleLines.length<3) group.append(svg('text',{x:15,y:p.h-13,class:'meta'},duty?state.department.name:(state.duty.links.find(l=>l.source===id)?.role || `참조 연결 ${adjacent(id).length}개`)));
      group.append(svg('title',{},duty?title:`${title} · ${prio.label}\n${prio.reason}`));
      const activate=()=>{if(duty){state.selected=null;showDuty();render();}else selectRegulation(id);};
      group.addEventListener('click',activate);group.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();activate();}});viewport.append(group);
    }
    transform();
    $('list-view').innerHTML=g.nodes.length?g.nodes.map(id=>{const r=regs.get(id);const links=D.relations.filter(e=>e.kind==='explicit'&&(e.from===g.center&&e.to===id||e.to===g.center&&e.from===id));const desc=state.focus?(links.length>1?'중심 규정과 서로 참조':links[0]?.from===id?'이 규정이 중심 규정을 참조':'중심 규정이 이 규정을 참조'):(state.duty.links.find(l=>l.source===id)?.role || '내용 검토 연결');return `<button class="list-card priority-${priority(id).level}" data-reg="${esc(id)}"><span>${badge(priority(id))}<small>${esc(r.category)} · ${esc(r.revision)}</small><strong>${esc(id)}</strong><small>${desc}</small><small class="priority-reason">${esc(priority(id).reason)}</small></span><span>↗</span></button>`;}).join(''):`<p class="empty">${g.total?'선택한 순서에 해당하는 연결이 없습니다. 전체 관계로 전환해보세요.':state.focus?'현재 추출된 규정 참조가 없습니다.':'이 업무의 내용 연결은 아직 검토하지 않았거나 재검토가 필요합니다.'}</p>`;
    if(!g.nodes.length) {const p=svg('text',{x:width/2,y:height/2+110,'text-anchor':'middle',fill:'#8b9890','font-size':14},g.total?'선택한 순서에 해당하는 연결이 없습니다':state.focus?'추출된 규정 참조가 없습니다':'이 업무의 내용 연결은 검토 전입니다');viewport.append(p);}
    $('graph').hidden=state.list;$('list-view').hidden=!state.list;
    $('view-toggle').textContent=state.list?'지도로 보기':'목록으로 보기';$('view-toggle').setAttribute('aria-pressed',state.list);
  }
  function articleDetail(source, number) {
    const matches=regs.get(source)?.articles.filter(a=>a.article===number) || [];
    return matches.map(a=>`<details><summary>${esc(number)} 전문 확인</summary><pre>${esc(a.body)}</pre></details>`).join('');
  }
  function evidenceCards(evidence) {
    return evidence.map(e=>`<div class="evidence-card"><div class="citation">${esc(e.source)} ${esc(e.article)}<br>${esc(e.title)}</div><p>${esc(e.quote)}</p><div class="source-label">${esc(e.revision || regs.get(e.source)?.revision)}</div>${articleDetail(e.source,e.article)}</div>`).join('');
  }
  function showDuty() {
    const d=state.duty,r=d.review;
    document.querySelector('.inspector').scrollTop=0;
    $('detail').innerHTML=`<h3>${esc(d.title)}</h3><p class="detail-description">${esc(r.scope)}</p>${r.status==='stale'?`<div class="notice">원문이 검토 당시와 달라 ${r.stale_links}개 연결을 숨겼습니다. 재검토 후에 다시 표시합니다.</div>`:''}${d.links.length?'<p class="selection-prompt">규정을 선택하면 연결 이유·적용 조건·근거 조문을 확인할 수 있습니다.</p>':'<div class="notice">미검토는 관련 규정이 없다는 뜻이 아닙니다. 검토하지 않은 연결을 추정해 표시하지 않습니다.</div>'}<h4>검토 범위</h4><p class="detail-description">${esc(r.reviewer || '내용 연결 미작성')}${r.reviewed_at?' · '+esc(r.reviewed_at):''}<br>${(r.gaps||[]).map(esc).join('<br>')}</p><details class="more-relations"><summary>업무분장 원문 확인</summary><div class="evidence-card"><div class="citation">${esc(D.meta.citation)}<br>${esc(state.department.id)} · ${esc(d.number)}번</div><p>${esc(d.text)}</p></div></details><div class="source-label">${esc(D.meta.organization_revision)}<br>부서 단위 분장업무이며 개인별 담당 지정은 아닙니다.</div>${D.meta.notices.length?'<button class="text-button" data-info>시행일 예외 확인 ↗</button>':''}`;
  }
  function revealDetail() {if(window.matchMedia('(max-width:1200px)').matches)document.querySelector('.inspector').scrollIntoView({behavior:'smooth',block:'start'});}
  function selectRegulation(id) {state.selected=id;showRegulation(id);render();revealDetail();}
  function showRegulation(id) {
    document.querySelector('.inspector').scrollTop=0;
    const r=regs.get(id), all=adjacent(id), otherCount=new Set(all.map(e=>e.from===id?e.to:e.from)).size;
    const link=state.duty.links.find(x=>x.source===id);
    const direct=state.focus && state.focus!==id?all.filter(e=>e.from===state.focus || e.to===state.focus):[];
    $('detail').innerHTML=`<h3>${esc(id)}</h3><span class="pill">${esc(link?.role || r.category)}</span><div class="revision">${esc(r.revision)}</div>${link?`<h4>업무와 연결되는 이유</h4><p class="detail-description">${esc(link.rationale)}</p><div class="condition"><strong>적용 조건</strong><p>${esc(link.condition)}</p></div><details class="more-relations"><summary>${esc(link.priority.label)} · 분류 이유</summary><p class="detail-description">${esc(link.priority.reason)}<br>업무별 읽기 순서이며 법적 효력의 우열을 뜻하지 않습니다.</p></details><div class="source-label">${esc(state.duty.review.reviewer)} · ${esc(state.duty.review.reviewed_at)}</div>`:'<p class="detail-description">선택한 업무와의 내용 연결은 미검토이며 확인 순서는 미평가입니다. 원문에 명시된 참조 관계를 탐색할 수 있습니다.</p>'}${direct.map(e=>`<button class="outline-button reference-action" data-edge-from="${esc(e.from)}" data-edge-to="${esc(e.to)}">${e.from===state.focus?'중심 규정에서 참조한 근거':'중심 규정을 참조하는 근거'} 보기</button>`).join('')}${otherCount?`<button class="primary-button" data-expand="${esc(id)}">연결된 다른 규정 보기 (${otherCount}종) ↗</button>`:'<p class="detail-description">현재 추출 방식으로 확인된 참조가 없습니다. 관계가 없다는 뜻은 아닙니다.</p>'}${link?`<h4>근거 조문</h4>${evidenceCards(link.evidence)}`:''}`;
  }
  function showEdge(e) {
    document.querySelector('.inspector').scrollTop=0;
    const review=e.content_review;
    $('detail').innerHTML=`<div class="detail-kicker">${review?'규정 관계 내용검토':'규정명 참조 자동 추출 · 내용 미검토'}</div><h3>${esc(e.from)}</h3><p class="detail-description">↓ ${esc(review?.label || '참조합니다')}</p><h3>${esc(e.to)}</h3>${!review?`<div class="source-label">참조 원문: ${esc(regs.get(e.from).revision)}<br>참조 대상: ${esc(regs.get(e.to).revision)}</div>`:''}${review?`<p class="detail-description">${esc(review.explanation)}</p><div class="source-label">${esc(review.reviewed_at)} · AI 내용검토, 기관 검토 전</div>${evidenceCards(review.evidence)}`:`<p class="detail-description">원문에 규정명이 명시되어 자동 추출한 참조입니다. 관계의 의미·적용 조건은 아직 내용 검토하지 않았습니다.</p>`}<details class="more-relations" ${review?'':'open'}><summary>자동 추출 참조 ${e.evidence.length}건 확인</summary>${e.evidence.map(v=>`<div class="evidence-card"><div class="citation">${esc(e.from)} ${esc(v.article)}<br>${esc(v.title)}</div><p>${esc(v.quote)}</p><div class="terms">대상: ${esc(e.to)} ${esc(v.target_article || '(규정명 참조)')}</div>${articleDetail(e.from,v.article)}</div>`).join('')}</details><button class="primary-button" data-expand="${esc(e.to)}">참조 대상 주변으로 이동 ↗</button><button class="outline-button" data-reg="${esc(e.from)}">출발 규정 살펴보기</button>`;
    revealDetail();
  }
  function expand(id) {if(!regs.has(id))return;$('priority-filter').value='all';state.focus=id;state.selected=id;state.page=0;resetZoom();render();showRegulation(id);saveState();if(window.matchMedia('(max-width:1200px)').matches)document.querySelector('main').scrollIntoView({behavior:'smooth',block:'start'});}
  function transform() {const {width=1100,height=720}=state.canvas||{};$('viewport').setAttribute('transform',`translate(${width/2+state.pan.x} ${height/2+state.pan.y}) scale(${state.zoom}) translate(${-width/2} ${-height/2})`);$('zoom-reset').textContent=Math.round(state.zoom*100)+'%';}
  function resetZoom() {state.zoom=1;state.pan={x:0,y:0};transform();}
  function info() {
    const r=D.meta.reviews;
    $('data-info').innerHTML=`<p>${D.departments.length}개 부서 · ${D.departments.reduce((n,d)=>n+d.duties.length,0)}개 업무 중 <strong>${r.reviewed_duties}개 업무에 내용 연결</strong>을 작성했습니다. ${r.unreviewed_duties}개 업무는 미검토, ${r.stale_duties}개 업무는 원문 변경으로 재검토가 필요합니다.</p><h4>업무 → 규정: 작성한 내용검토</h4><p>업무분장과 목적·적용범위·관련 조문 전문을 읽고 규정의 역할, 연결 이유, 적용 조건을 작성했습니다. 단어 일치나 유사도 점수로 연결을 생성하지 않습니다. ${esc(r.reviewer)} · ${esc(r.reviewed_at)}. 부분 검토이며 기관의 공식 적용 판정은 아닙니다.</p><h4>규정 → 규정: 두 종류의 근거</h4><p>전체 ${D.relations.length}개 규정명 참조는 본칙에서 자동 추출했습니다. 그중 ${r.reviewed_relations}개 관계에는 조문을 읽고 작성한 관계 설명을 추가했습니다. 자동 추출만 된 관계와 내용검토 관계를 패널에서 구분합니다.</p><h4>무엇부터 확인하나요?</h4><p>먼저 확인: 선택한 업무를 직접 규율하는 기본 규정. 함께 확인: 실행 단계의 절차·기준. 해당할 때: 특정 사업요소나 수행방식의 분기입니다. 이유와 순서를 업무별로 작성했으며, 인용 건수·유사도 점수는 사용하지 않습니다. 조건부 규정도 해당 상황에서는 중요합니다. 규정 중심 화면에서 현재 업무의 판단이 없는 이웃은 순서 미평가입니다.</p><h4>개정 시 재검토</h4><p>업무분장, 근거 규정 파일, 조문 본문의 해시를 검토 당시 값과 비교합니다. 원문이 바뀌면 영향을 받는 연결을 숨기고 재검토 필요로 표시합니다. 빌드는 검토 기록을 자동 갱신하지 않습니다.</p><details><summary>실제로 읽은 조문 범위</summary>${r.reading_scope.map(s=>`<p><strong>${esc(s.source)}</strong> · ${esc(s.revision)}<br>${s.articles.map(esc).join(', ')}</p>`).join('')}</details><h4>범위와 한계</h4><p>${esc(D.meta.limits)}<br>별표 4 업무분장 기반이며 해외사무소 별표 5는 별도 모델링하지 않았습니다. 실시간 조회가 아닌 저장소 스냅샷이며 ALIO 공식 원문의 최신성은 별도로 확인하지 않았습니다. 미검토 영역은 업무마다 표시합니다.</p>${D.meta.notices.length?`<h4>업무분장 시행일 예외 — 원문</h4>${D.meta.notices.map(n=>`<p class="notice">${esc(n)}</p>`).join('')}`:''}`;
    $('info-dialog').showModal();
  }
  $('department-search').addEventListener('input',e=>openDepartmentSearch(e.target.value));
  $('department-search').addEventListener('focus',e=>{
    openDepartmentSearch(e.target.value===state.department?.name?'':e.target.value,true);
    e.target.select();
  });
  $('department-search').addEventListener('click',e=>{
    if($('department-results').hidden) openDepartmentSearch(e.target.value===state.department?.name?'':e.target.value,true);
  });
  $('department-search').addEventListener('compositionstart',()=>{departmentComposing=true;});
  $('department-search').addEventListener('compositionend',()=>{departmentComposing=false;});
  $('department-search').addEventListener('keydown',e=>{
    // The Enter used to finish a Korean IME syllable must not choose a department.
    if(e.isComposing || departmentComposing || e.keyCode===229) return;
    if(e.key==='Enter') {e.preventDefault();if(!$('department-results').hidden)commitDepartment(departmentActive);}
    else if(e.key==='Escape') {e.preventDefault();closeDepartmentSearch();}
    else if(e.key==='ArrowDown' || e.key==='ArrowUp') {
      e.preventDefault();
      if($('department-results').hidden) openDepartmentSearch(e.target.value===state.department?.name?'':e.target.value,true);
      else if(departmentResults.length) highlightDepartment(Math.max(0,Math.min(departmentResults.length-1,departmentActive+(e.key==='ArrowDown'?1:-1))),true);
    }
  });
  $('department-results').addEventListener('mousedown',e=>{if(e.target.closest('[role="option"]'))e.preventDefault();});
  $('department-results').addEventListener('click',e=>{const row=e.target.closest('[data-department-index]');if(row)commitDepartment(Number(row.dataset.departmentIndex));});
  document.querySelector('.department-picker').addEventListener('focusout',e=>{if(!e.currentTarget.contains(e.relatedTarget))closeDepartmentSearch();});
  document.addEventListener('pointerdown',e=>{if(!e.target.closest('.department-picker'))closeDepartmentSearch();});
  $('duties').addEventListener('click',e=>{const b=e.target.closest('[data-duty]');if(!b)return;state.duty=state.department.duties.find(d=>d.id===b.dataset.duty);state.focus=null;state.selected=null;state.page=0;$('priority-filter').value='all';resetZoom();renderDuties();render();showDuty();saveState();});
  document.addEventListener('click',e=>{
    const b=e.target.closest('button');
    if(b?.dataset.expand)expand(b.dataset.expand);
    else if(b?.dataset.reg)selectRegulation(b.dataset.reg);
    else if(b?.dataset.edgeFrom){const edge=D.relations.find(x=>x.from===b.dataset.edgeFrom&&x.to===b.dataset.edgeTo);if(edge)showEdge(edge);}
    else if(b?.hasAttribute('data-info'))info();
    if(!e.target.closest('.reg-search'))$('search-results').hidden=true;
  });
  $('regulation-search').addEventListener('input',e=>{
    const q=e.target.value.trim();if(!q){$('search-results').hidden=true;return;}
    const matches=D.regulations.filter(r=>r.id.includes(q)).slice(0,15);
    $('search-results').innerHTML=matches.length?matches.map(r=>`<button data-search-reg="${esc(r.id)}">${esc(r.id)}<small>${esc(r.category)} · ${esc(r.revision)}</small></button>`).join(''):'<p class="empty">일치하는 규정이 없습니다.</p>';
    $('search-results').hidden=false;
  });
  $('search-results').addEventListener('click',e=>{const b=e.target.closest('[data-search-reg]');if(b){expand(b.dataset.searchReg);$('search-results').hidden=true;$('regulation-search').value='';}});
  $('regulation-search').addEventListener('keydown',e=>{if(e.key==='Escape')$('search-results').hidden=true;if(e.key==='ArrowDown'){$('search-results').querySelector('button')?.focus();e.preventDefault();}});
  $('reset').addEventListener('click',()=>{state.focus=null;state.selected=null;state.page=0;$('priority-filter').value='all';resetZoom();render();showDuty();saveState();});
  $('show-reviewed').addEventListener('change',render);
  $('priority-filter').addEventListener('change',()=>{state.selected=null;state.page=0;render();state.focus?showRegulation(state.focus):showDuty();});
  for(const [id,step] of [['previous-neighbors',-1],['next-neighbors',1]]) $(id).addEventListener('click',()=>{state.page+=step;state.selected=state.focus;render();showRegulation(state.focus);});
  $('view-toggle').addEventListener('click',()=>{state.list=!state.list;render();});
  $('zoom-in').addEventListener('click',()=>{state.zoom=Math.min(2.5,state.zoom+.2);transform();});
  $('zoom-out').addEventListener('click',()=>{state.zoom=Math.max(.4,state.zoom-.2);transform();});
  $('zoom-reset').addEventListener('click',resetZoom);
  let drag=null;
  $('map-svg').addEventListener('pointerdown',e=>{if(e.target.closest('[role=button]'))return;const matrix=$('map-svg').getScreenCTM();if(!matrix)return;drag={x:e.clientX,y:e.clientY,px:state.pan.x,py:state.pan.y,scale:matrix.a};$('map-svg').setPointerCapture(e.pointerId);});
  $('map-svg').addEventListener('pointermove',e=>{if(drag){state.pan={x:drag.px+(e.clientX-drag.x)/drag.scale,y:drag.py+(e.clientY-drag.y)/drag.scale};transform();}});
  ['pointerup','pointercancel'].forEach(type=>$('map-svg').addEventListener(type,()=>{drag=null;}));
  $('about').addEventListener('click',info);$('close-info').addEventListener('click',()=>$('info-dialog').close());
  $('org-revision').textContent=D.meta.organization_revision;
  $('review-coverage').textContent=`업무 내용검토 ${D.meta.reviews.reviewed_duties}/${D.departments.reduce((n,d)=>n+d.duties.length,0)}`;
  const initial=new URLSearchParams(location.hash.slice(1));
  const defaultDepartment=D.departments.find(d=>d.name==='동남아시아1팀')||D.departments[0];
  const initialDepartment=D.departments.find(d=>d.id===initial.get('department'))||defaultDepartment;
  const initialDuty=initial.get('duty')||initialDepartment.duties.find(d=>d.title.includes('국별협력사업 기획'))?.id;
  selectDepartment(initialDepartment.id,initialDuty);
  if(regs.has(initial.get('regulation')))expand(initial.get('regulation'));
})();
