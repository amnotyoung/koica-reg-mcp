const assert = require('node:assert/strict');
const M = require('../regulation_map/map_model.js');
const make = (source,level,order) => ({source,priority:{level,order,reason:'검토 이유'}});
const links=[make('조건부','conditional',3),make('기본','core',1),make('실행','supporting',2)];
assert.deepEqual(M.ordered(links,['미평가','조건부','실행','기본']),['기본','실행','조건부','미평가']);
assert.deepEqual(M.ordered(links,['조건부','실행','기본'],'core'),['기본']);
assert.equal(M.priority(links,'미평가').level,'unassessed');
assert.equal(M.priority([make('조건부','core',1)],'조건부').level,'core');
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
