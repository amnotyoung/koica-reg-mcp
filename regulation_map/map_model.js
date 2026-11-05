/* Pure display model. Priority comes only from the authored duty review. */
(function (root) {
  const levels = {
    core: {rank: 1, label: '먼저 확인'},
    supporting: {rank: 2, label: '함께 확인'},
    conditional: {rank: 3, label: '해당할 때'},
    unassessed: {rank: 4, label: '순서 미평가'},
  };
  function priority(links, id) {
    const authored = links.find(link => link.source === id)?.priority;
    return authored ? {...levels[authored.level], ...authored} : {...levels.unassessed, level:'unassessed', order:Infinity, reason:''};
  }
  function ordered(links, ids, filter='all') {
    return [...ids].filter(id => filter === 'all' || priority(links,id).level === filter)
      .sort((a,b) => {
        const x=priority(links,a),y=priority(links,b);
        return x.rank-y.rank || x.order-y.order || a.localeCompare(b,'ko');
      });
  }
  function dutyLayout(links, ids) {
    const groups=['core','supporting','conditional'].map(level => ({level, ids:ids.filter(id=>priority(links,id).level===level)}));
    const height=Math.max(720, Math.max(...groups.map(g=>g.ids.length))*116+170);
    const positions=new Map([['@duty',{x:145,y:height/2+30,w:226,h:112}]]);
    const bands=[];
    const sizes={core:{w:256,h:112},supporting:{w:226,h:100},conditional:{w:204,h:94}};
    groups.forEach((group,i) => {
      const x=[470,835,1195][i], size=sizes[group.level];
      bands.push({x:x-155,width:310,level:group.level,count:group.ids.length,label:levels[group.level].label});
      group.ids.forEach((id,j)=>positions.set(id,{x,y:height/2+30+(j-(group.ids.length-1)/2)*116,...size}));
    });
    return {width:1400,height,positions,bands};
  }
  function visibleReferences(relations, ids, selected) {
    const visible=new Set(ids);
    if(!selected || !visible.has(selected)) return [];
    return relations.filter(edge => (edge.from===selected || edge.to===selected) &&
      visible.has(edge.from) && visible.has(edge.to));
  }
  function referencePage(relations, links, center, filter='all', page=0, size=12) {
    const ids=[...new Set(relations.filter(e=>e.from===center || e.to===center)
      .map(e=>e.from===center?e.to:e.from))];
    const matches=ordered(links,ids,filter),pages=Math.max(1,Math.ceil(matches.length/size));
    const current=Math.max(0,Math.min(page,pages-1));
    return {nodes:matches.slice(current*size,(current+1)*size),total:ids.length,matched:matches.length,page:current,pages};
  }
  function departmentMatches(departments, query) {
    const normalize=text=>text.normalize('NFC').toLocaleLowerCase().replace(/[\s·‧ㆍ/]+/g,'');
    const q=normalize(query.trim());
    return departments.map((department,index)=>{
      const name=normalize(department.name),path=normalize(department.id),division=normalize(department.division);
      const rank=!q?0:name===q?0:name.startsWith(q)?1:name.includes(q)?2:path.includes(q)?3:division.includes(q)?4:Infinity;
      return {department,index,rank};
    }).filter(x=>Number.isFinite(x.rank)).sort((a,b)=>a.rank-b.rank||a.index-b.index).map(x=>x.department);
  }
  function organizationalChoices(departments) {
    const parents=new Map(),existing=new Set(departments.map(d=>d.id));
    for(const department of departments) {
      const path=department.path || department.id.split(' / ');
      for(let depth=1;depth<path.length;depth++) {
        const parentPath=path.slice(0,depth),id=parentPath.join(' / ');
        if(existing.has(id))continue;
        if(!parents.has(id))parents.set(id,{id,name:parentPath.at(-1),path:parentPath,
          division:department.division,aggregate:true,children:[],duties:[]});
        const parent=parents.get(id);
        parent.children.push({id:department.id,name:department.name});
        parent.duties.push(...department.duties.map(duty=>({...duty,
          id:`${department.id}::${duty.id}`,
          owner_department:{id:department.id,name:department.name,path},original_duty_id:duty.id})));
      }
    }
    // These are navigation groupings only: the original duties, evidence and
    // review counts remain owned by the leaf departments.
    return [...parents.values(),...departments];
  }
  function departmentOverview(department) {
    const bySource=new Map();
    const coverage={total:department.duties.length,linked:0,stale:0,unreviewed:0};
    for(const duty of department.duties) {
      if(duty.links.length)coverage.linked++;
      if(duty.review?.status==='stale')coverage.stale++;
      if(duty.review?.status==='unreviewed')coverage.unreviewed++;
      const owner=duty.owner_department || department;
      for(const link of duty.links) {
        if(!bySource.has(link.source))bySource.set(link.source,{source:link.source,associations:[],evidence:[]});
        const merged=bySource.get(link.source);
        merged.associations.push({department_id:owner.id,department_name:owner.name,
          duty_id:duty.id,original_duty_id:duty.original_duty_id || duty.id,
          duty_title:duty.title,number:duty.number,review:duty.review,link});
        for(const evidence of link.evidence || []) {
          if(!merged.evidence.some(e=>e.source===evidence.source && e.article===evidence.article && e.quote===evidence.quote && e.body_sha256===evidence.body_sha256))merged.evidence.push(evidence);
        }
      }
    }
    const links=[...bySource.values()].map(merged=>{
      const best=[...merged.associations].sort((a,b)=>levels[a.link.priority.level].rank-levels[b.link.priority.level].rank || a.link.priority.order-b.link.priority.order)[0].link.priority;
      const teamCount=new Set(merged.associations.map(a=>a.department_id)).size;
      return {...merged,team_count:teamCount,role:`${teamCount}개 부서 · ${merged.associations.length}개 업무`,
        priority:{level:best.level,label:levels[best.level].label,order:best.order,
          reason:`연결된 업무별 판단 중 가장 높은 단계인 '${levels[best.level].label}'로 배치했습니다. 적용 조건은 각 업무에서 확인할 수 있습니다.`}};
    });
    links.sort((a,b)=>levels[a.priority.level].rank-levels[b.priority.level].rank || a.source.localeCompare(b.source,'ko'));
    return {id:'@overview',overview:true,title:`${department.name} 전체 규정 지도`,number:'전체',links,coverage,
      review:{status:coverage.stale?'stale':coverage.linked?'partial':'unreviewed'}};
  }
  function departmentSelection(department, dutyId) {
    // Old deep links still open the requested task; a fresh department selection
    // always opens the complete department, including leaf teams.
    return department.duties.find(d=>d.id===dutyId) || departmentOverview(department);
  }
  const api={levels,priority,ordered,dutyLayout,visibleReferences,referencePage,departmentMatches,organizationalChoices,departmentOverview,departmentSelection};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.MapPriority=api;
})(typeof window!=='undefined'?window:this);
