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
  const api={levels,priority,ordered,dutyLayout,visibleReferences,referencePage};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.MapPriority=api;
})(typeof window!=='undefined'?window:this);
