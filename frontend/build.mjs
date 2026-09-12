import * as esbuild from 'esbuild';
import {copyFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const base = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(base, '../src/corridor_context/review_ui');
await mkdir(out, {recursive:true});
await copyFile(path.join(base,'index.html'),path.join(out,'index.html'));
const watch = process.argv.includes('--watch');
const options = {absWorkingDir:base, entryPoints:{app:'src/main.jsx',style:'src/style.css'}, outdir:out, bundle:true, minify:!watch, jsx:'automatic', define:{'process.env.NODE_ENV':JSON.stringify(watch?'development':'production')}, logLevel:'info'};
if (watch) {const ctx=await esbuild.context(options); await ctx.watch(); console.log('Watching React/CSS. Run the Python review server and refresh your browser after changes.');}
else await esbuild.build(options);
