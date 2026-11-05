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
  const api={levels,priority,ordered,dutyLayout,visibleReferences,referencePage,departmentMatches,organizationalChoices};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.MapPriority=api;
})(typeof window!=='undefined'?window:this);
