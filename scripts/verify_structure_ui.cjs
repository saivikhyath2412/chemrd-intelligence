// Exercise the actual shared views against an isolated local database.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/saivi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'), out=path.join(root,'.smoke-current');
fs.mkdirSync(out,{recursive:true});
const server=spawn(process.env.CHEMRD_TEST_PYTHON || 'C:/Users/saivi/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',['-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8917'],{cwd:root,env:{...process.env,DATABASE_URL:'sqlite:///'+path.join(out,'ui-test.db').replaceAll('\\','/'),CHEMRD_SEED:'true',CHEMRD_CLEAR_SEED_DATA:'false',CHEMRD_STRUCTURE_CACHE:path.join(out,'structures'),HINDSIGHT_ENABLED:'false',OPENAI_API_KEY:'',GEMINI_API_KEY:'',GROQ_API_KEY:''},stdio:'ignore',windowsHide:true});
let browser;
(async()=>{
  const url='http://127.0.0.1:8917';
  for(let n=0;n<60;n++){try{if((await fetch(url+'/api/health')).ok)break;}catch(_){}await new Promise(r=>setTimeout(r,250));}
  browser=await chromium.launch({executablePath:process.env.CHEMRD_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,args:['--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1366,height:768}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);
  await page.locator('.auth-card').waitFor();
  for(const size of [{width:1366,height:768},{width:900,height:600},{width:480,height:800}]){
    await page.setViewportSize(size);
    for(const mode of ['login','register']){
      await page.evaluate(mode=>showAuthScreen(mode),mode);
      await page.waitForFunction(()=>{const r=document.querySelector('.auth-card').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth;});
      const fit=await page.evaluate(()=>({scroll:document.documentElement.scrollHeight<=innerHeight,overflow:getComputedStyle(document.body).overflow}));
      assert(fit.scroll&&fit.overflow==='hidden',JSON.stringify({mode,size,fit}));
    }
  }
  await page.setViewportSize({width:1366,height:768});
  await page.evaluate(()=>showAuthScreen('register'));
  await page.locator('#auth-username').fill('ui-'+Date.now());
  await page.locator('#auth-password').fill('test-structure-123');
  await page.locator('#auth-confirm-password').fill('test-structure-123');
  await page.locator('.auth-submit').click();
  await page.locator('#main-nav').waitFor();
  const records=fs.readdirSync(path.join(out,'structures')).filter(f=>f.startsWith('identity-v4-')).map(f=>JSON.parse(fs.readFileSync(path.join(out,'structures',f))));
  const cap=records.find(r=>r.query_name.toLowerCase()==='capsaicin');
  assert(cap,'Run verify_structures.py before this UI check');
  await page.route('**/api/search?*',route=>route.fulfill({json:{query:'Capsaicin',results:[cap],live:{results:[cap]}}}));
  await page.evaluate(()=>navigate('search'));
  await page.locator('#search-input').fill('Capsaicin');
  await page.evaluate(()=>runSearch());
  await page.evaluate(id=>openLiveChemical(id),cap.id);
  async function verify(id){
    const host=page.locator('#'+id);
    await host.waitFor({state:'attached'});
    await page.waitForFunction(id=>document.getElementById(id)?.dataset.ready==='true',id,{timeout:70000});
    await page.waitForFunction(()=>[...document.querySelectorAll('img.structure-image')].every(i=>i.complete&&i.naturalWidth>0&&!i.hidden));
    const pixels=await host.locator('canvas').evaluate(c=>({width:c.width,height:c.height}));
    assert(pixels.width>0&&pixels.height>0);
  }
  await verify('mol3d-'+cap.id);
  await page.screenshot({path:path.join(out,'capsaicin.png'),fullPage:true});
  // Bearer recovery when a desktop child browser loses the cookie.
  await page.context().clearCookies();
  await page.evaluate(id=>openLiveChemical(id),cap.id);
  await verify('mol3d-'+cap.id);
  const download=page.waitForEvent('download');
  await page.locator('a:has-text("Download SDF")').click();
  assert((await download).suggestedFilename().endsWith('.sdf'));
  const saved=await page.evaluate(async record=>{const folders=await api('/api/library/folders');return api('/api/library/chemicals',{method:'POST',body:JSON.stringify({folder_id:folders[0].id,record})});},cap);
  await page.evaluate(id=>showChemical(id),saved.chemical_id);
  await verify('mol3d-'+saved.chemical_id);
  await page.evaluate(()=>navigate('graph'));
  await page.locator('#graph-inspector').waitFor();
  await page.evaluate(record=>renderGraphChemicalInspector(graphChemicalRecord(record)),cap);
  await verify('mol3d-graph-inspector');
  const sim=await page.evaluate(async record=>api('/api/simulations',{method:'POST',body:JSON.stringify({experiment:{name:'Structure history test',objective:'UI verification',chemicals:[],globalConditions:{}},simulation_results:{feasibility_score:0,reaction_type:'UI fixture, not a simulation prediction',products:[],main_product_details:{name:record.name,smiles:record.smiles,formula:record.formula}}})}),cap);
  await page.evaluate(id=>openSavedSimulation(id),sim.id);
  await verify('sim-mol3d-container');
  await page.screenshot({path:path.join(out,'simulation-structures.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: login/register fit 3 window sizes; search, library, graph, saved simulation 2D+3D; cookie-loss recovery; SDF download; no JS errors.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();});
