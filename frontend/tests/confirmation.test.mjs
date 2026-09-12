import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const result=await build({entryPoints:['src/components/AppliedConfirmation.jsx'],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',external:['react','react-dom'],logLevel:'silent'});
const module={exports:{}};
new Function('require','module','exports',result.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const {resolutionChanges}=module.exports;
const state=facts=>JSON.stringify({facts,definitions:{employee_count:{label:'Employee count'}}});
const fact=(value,display_value)=>({value,display_value});
test('receipt compares committed before and after values, omitting unchanged facts',()=>{
 const changes=resolutionChanges({before_json:state({employee_count:fact(20,'20 employees'),budget:fact(500,'$500')}),after_json:state({employee_count:fact(25,'25 employees'),budget:fact(500,'$500')})});
 assert.deepEqual(changes,[{key:'employee_count',label:'Employee count',before:'20 employees',after:'25 employees'}]);
});
test('receipt describes additions and removals',()=>{
 assert.equal(resolutionChanges({before_json:state({}),after_json:state({employee_count:fact(25,'25 employees')})})[0].before,'Not recorded');
 assert.equal(resolutionChanges({before_json:state({employee_count:fact(25,'25 employees')}),after_json:state({})})[0].after,'Removed (history retained)');
});
test('ignore and reaffirmation do not invent value changes',()=>{
 const facts={employee_count:fact(25,'25 employees')};
 assert.deepEqual(resolutionChanges({before_json:state(facts),after_json:state({...facts,employee_count:{...facts.employee_count,version_id:99}})}),[]);
});
