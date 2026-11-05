const assert = require('node:assert/strict');
const M = require('../regulation_map/map_model.js');
const make = (source,level,order) => ({source,priority:{level,order,reason:'검토 이유'}});
const links=[make('조건부','conditional',3),make('기본','core',1),make('실행','supporting',2)];
assert.deepEqual(M.ordered(links,['미평가','조건부','실행','기본']),['기본','실행','조건부','미평가']);
assert.deepEqual(M.ordered(links,['조건부','실행','기본'],'core'),['기본']);
assert.equal(M.priority(links,'미평가').level,'unassessed');
assert.equal(M.priority([make('조건부','core',1)],'조건부').level,'core');
const departments=[
  {id:'인사실 / 인사교육팀',name:'인사교육팀',division:'경영전략본부'},
  {id:'인사실 / 인사지원팀',name:'인사지원팀',division:'경영전략본부'},
  {id:'안전·경영실 / 재무회계팀',name:'재무회계팀',division:'경영전략본부'},
  {id:'ESG경영실 / ESG·소통팀',name:'ESG·소통팀',division:'직속부서'},
];
assert.deepEqual(M.departmentMatches(departments,'인사').map(d=>d.name),['인사교육팀','인사지원팀']);
assert.equal(M.departmentMatches(departments,' 재무 회계 팀 ')[0].name,'재무회계팀');
assert.equal(M.departmentMatches(departments,'esg 소통')[0].name,'ESG·소통팀');
assert.equal(M.departmentMatches(departments,'경영전략본부').length,3);
assert.equal(M.departmentMatches(departments,'없는부서').length,0);
assert.equal(M.departmentMatches(departments,'').length,4);
assert.equal(M.departmentMatches([{id:'가',name:'회계팀지원',division:'본부'},departments[2],{id:'나',name:'회계팀',division:'본부'}],'회계팀')[0].name,'회계팀');
const leaves=['인공지능전환·정보화팀','데이터팀','정보보안팀'].map((name,i)=>({
  id:'인공지능전환실 / '+name,name,path:['인공지능전환실',name],division:'경영전략본부',
  duties:[{id:'d1',number:'1',title:name+' 업무',links:[make('근거'+i,'core',1)],review:{status:'partial'}}]
}));
const original=JSON.stringify(leaves),choices=M.organizationalChoices(leaves);
const parent=M.departmentMatches(choices,'인공지능전환실')[0];
assert.equal(parent.id,'인공지능전환실');
assert.equal(parent.aggregate,true);
assert.equal(parent.children.length,3);
assert.equal(new Set(parent.duties.map(d=>d.id)).size,3);
assert.equal(parent.duties[1].owner_department.name,'데이터팀');
assert.equal(parent.duties[1].original_duty_id,'d1');
assert.equal(parent.duties[1].links,leaves[1].duties[0].links);
assert.equal(M.departmentMatches(choices,'정보보안팀')[0].id,leaves[2].id);
assert.equal(JSON.stringify(leaves),original);
const nested=M.organizationalChoices([{id:'처 / 실 / 팀',name:'팀',division:'본부',path:['처','실','팀'],duties:leaves[0].duties}]);
assert.deepEqual(nested.map(d=>d.id),['처','처 / 실','처 / 실 / 팀']);
const relations=[{from:'기본',to:'실행'},{from:'실행',to:'조건부'},{from:'기본',to:'밖'}];
assert.deepEqual(M.visibleReferences(relations,['기본','실행','조건부'],null),[]);
assert.deepEqual(M.visibleReferences(relations,['기본','실행','조건부'],'기본'),[relations[0]]);
assert.deepEqual(M.visibleReferences(relations,['기본','실행','조건부'],'실행'),relations.slice(0,2));
assert.deepEqual(M.visibleReferences(relations,['실행','조건부'],'기본'),[]);
const extended=Array.from({length:19},(_,i)=>({from:'기본',to:'규정'+String(i).padStart(2,'0')}));
const page1=M.referencePage(extended,[],'기본'),page2=M.referencePage(extended,[],'기본','all',1);
assert.equal(page1.nodes.length,12);
assert.equal(page2.nodes.length,7);
assert.equal(new Set([...page1.nodes,...page2.nodes]).size,19);
assert.equal(M.referencePage(extended,[],'기본','core',1).page,0);
assert.equal(M.referencePage(relations,links,'기본').nodes.includes('밖'),true);
for (const count of [0,1,7,12]) {
  const many=[make('기본','core',1),...Array.from({length:count},(_,i)=>make('실행'+i,'supporting',i+2)),make('조건부','conditional',count+2)];
  const layout=M.dutyLayout(many,M.ordered(many,many.map(l=>l.source)));
  const boxes=[...layout.positions.values()];
  for(let i=0;i<boxes.length;i++) {
    const a=boxes[i];
    assert(a.x-a.w/2>=0 && a.x+a.w/2<=layout.width);
    assert(a.y-a.h/2>=100 && a.y+a.h/2<=layout.height);
    for(let j=i+1;j<boxes.length;j++) {
      const b=boxes[j];
      assert(Math.abs(a.x-b.x)>=(a.w+b.w)/2 || Math.abs(a.y-b.y)>=(a.h+b.h)/2,'overlapping nodes');
    }
  }
  assert(layout.positions.get('기본').w>layout.positions.get('조건부').w);
}
console.log('Map priority ordering, filtering, task context, and layout checks passed');
