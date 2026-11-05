const assert = require('node:assert/strict');
const M = require('../regulation_map/map_model.js');
const make = (source,level,order) => ({source,priority:{level,order,reason:'검토 이유'}});
const links=[make('조건부','conditional',3),make('기본','core',1),make('실행','supporting',2)];
assert.deepEqual(M.ordered(links,['미평가','조건부','실행','기본']),['기본','실행','조건부','미평가']);
assert.deepEqual(M.ordered(links,['조건부','실행','기본'],'core'),['기본']);
assert.equal(M.priority(links,'미평가').level,'unassessed');
assert.equal(M.priority([make('조건부','core',1)],'조건부').level,'core');
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
