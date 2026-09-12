import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const result=await build({entryPoints:['src/App.jsx'],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',external:['react','react-dom'],logLevel:'silent'});
const module={exports:{}};
new Function('require','module','exports',result.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const {ReviewCard}=module.exports;
const review={id:1,meeting_id:'test',segment_index:0,reason:'Ambiguous speaker',excerpt:'<script>alert(1)</script>',snapshot:{segment:{speaker:{name:'Sam'}},context:[],attribution:{model:'test-model',version:'v1'},evidence:[{index:0,text:'Supporting statement'}]},candidates:[{label:'Employee count',display_value:'25 employees',subject_client_key:'acme-benefits'}]};
test('shows values, source, model and evidence without treating transcript as HTML',()=>{
 const html=renderToStaticMarkup(createElement(ReviewCard,{review}));
 assert.match(html,/25 employees/);assert.match(html,/Supporting statement/);assert.match(html,/test-model/);
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
});
test('unstructured and legacy cases stay visible',()=>{
 const html=renderToStaticMarkup(createElement(ReviewCard,{review:{...review,candidates:[],snapshot:{}}}));
 assert.match(html,/Manual interpretation needed/);assert.match(html,/Offline rules/);assert.match(html,/unavailable/);
});

test('full transcript includes distant passages and timestamps with a flagged marker',()=>{
 const transcript={origin:'captured',flagged_index:1,segments:[
  {index:0,text:'Opening remarks',speaker:{name:'Pat'},start_time:'2026-09-12T10:00:00Z'},
  {index:1,text:'Ambiguous statement',speaker:{name:'Sam'}},
  {index:5,text:'Final passage outside nearby context',speaker:{name:'Pat'}}
 ]};
 const html=renderToStaticMarkup(createElement(ReviewCard,{review:{...review,transcript}}));
 assert.match(html,/Full transcript/);assert.match(html,/Final passage outside nearby context/);
 assert.match(html,/2026-09-12T10:00:00Z/);assert.match(html,/Flagged/);
});
test('older transcript versions carry an explicit warning',()=>{
 const html=renderToStaticMarkup(createElement(ReviewCard,{review:{...review,transcript:{origin:'latest',segments:[{index:0,text:'Latest stored text'}],flagged_index:null}}}));
 assert.match(html,/latest stored meeting transcript/);assert.match(html,/may differ/);
});
test('open reviews expose correction instructions and ignore action',()=>{
 const html=renderToStaticMarkup(createElement(ReviewCard,{review,customerKey:'acme-benefits',csrfToken:'test',onResolved:()=>{}}));
 assert.match(html,/Correction instructions/);assert.match(html,/Preview correction/);assert.match(html,/Ignore without changes/);
});
test('resolved reviews show audit details without correction inputs',()=>{
 const resolved={...review,status:'ignored',resolution:{summary:'Ignored without changing facts.',instruction:'Wrong employer',reviewer:'Sam',created_at:'2026-09-12'}};
 const html=renderToStaticMarkup(createElement(ReviewCard,{review:resolved,customerKey:'acme-benefits'}));
 assert.match(html,/Wrong employer/);assert.match(html,/Sam/);assert.doesNotMatch(html,/Correction instructions/);
});
test('each open item offers agent advice and saved clarification has no assumed instructions',()=>{
 const base={review,customerKey:'acme-benefits',csrfToken:'test'};
 assert.match(renderToStaticMarkup(createElement(ReviewCard,base)),/Suggest resolution/);
 const suggestion={action:'clarify',rationale:'Preference is not an approved budget.',question:'Is $550 approved for Apex?',suggested_instruction:'',evidence:['We prefer that too'],model:'test',created_at:'2026-09-12',stale:false};
 const html=renderToStaticMarkup(createElement(ReviewCard,{...base,review:{...review,suggestion}}));
 assert.match(html,/Is \$550 approved for Apex/);assert.match(html,/We prefer that too/);
 assert.doesNotMatch(html,/Use suggested instructions/);
});
test('saved actionable advice can be adopted, while stale advice is marked for refresh',()=>{
 const suggestion={action:'ignore',rationale:'Other employer.',question:'',suggested_instruction:'Ignore this item.',evidence:['Their budget'],model:'test',created_at:'2026-09-12',stale:true};
 const html=renderToStaticMarkup(createElement(ReviewCard,{review:{...review,suggestion},customerKey:'acme-benefits'}));
 assert.match(html,/Use suggested instructions/);assert.match(html,/Refresh recommendation/);assert.match(html,/Facts or source context changed/);
});
test('actionable AI suggestion has an accept button; clarification has none',()=>{
 const suggestion={id:1,action:'ignore',rationale:'Other employer.',question:'',suggested_instruction:'Ignore this item.',evidence:[],model:'test',created_at:'today',stale:false};
 const render=s=>renderToStaticMarkup(createElement(ReviewCard,{review:{...review,suggestion:s},customerKey:'acme-benefits'}));
 assert.match(render(suggestion),/Accept AI suggestion/);
 assert.doesNotMatch(render({...suggestion,action:'clarify',question:'Which employer?',suggested_instruction:''}),/Accept AI suggestion/);
});
