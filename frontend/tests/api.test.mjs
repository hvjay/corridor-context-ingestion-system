import test from 'node:test';
import assert from 'node:assert/strict';
import {post} from '../src/api.js';
const response=(status,data)=>({ok:status===200,status,json:async()=>data});
test('missing token is obtained before submitting',async t=>{
 const calls=[];
 t.mock.method(globalThis,'fetch',async(path,options)=>{calls.push([path,options]);return path==='/api/customers'?response(200,{csrfToken:'fresh'}):response(200,{id:1});});
 assert.deepEqual(await post('/write',{requestId:'same'},''),{id:1});
 assert.equal(calls[1][1].headers['X-CSRF-Token'],'fresh');
});
test('old backend gets an actionable error and no mutation request',async t=>{
 const mock=t.mock.method(globalThis,'fetch',async()=>response(200,{customers:[]}));
 await assert.rejects(post('/write',{},''),/Restart corridor-review/);
 assert.equal(mock.mock.callCount(),1);
});
test('expired session refreshes token and retries identical payload once',async t=>{
 const bodies=[];
 t.mock.method(globalThis,'fetch',async(path,options)=>{
  if(path==='/api/customers')return response(200,{csrfToken:'new'});
  bodies.push(options.body);
  return options.headers['X-CSRF-Token']==='new'?response(200,{id:1}):response(403,{error:'Expired'});
 });
 assert.deepEqual(await post('/write',{requestId:'stable'},'old'),{id:1});
 assert.equal(bodies.length,2);assert.equal(bodies[0],bodies[1]);
});
