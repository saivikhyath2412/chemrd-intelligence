// One molecular viewer for search, library, graph, and simulation history.
window.moleculeViewers = Object.create(null);
async function recoverStructureImage(img) {
  if (img.dataset.recovering) {
    img.hidden = true;
    if (img.nextElementSibling) img.nextElementSibling.hidden = false;
    return;
  }
  img.dataset.recovering = 'true';
  const fallback = img.nextElementSibling;
  try {
    const response = await fetch(img.getAttribute('src'), {headers:authHeaders(), credentials:'same-origin'});
    if (!response.ok) throw new Error('Structure lookup failed');
    const previous = img.dataset.blob;
    const blob = URL.createObjectURL(await response.blob());
    img.dataset.blob = blob;
    img.src = blob;
    img.hidden = false;
    if (fallback) fallback.hidden = true;
    if (previous) URL.revokeObjectURL(previous);
  } catch (_) {
    img.hidden = true;
    if (fallback) {
      fallback.hidden = false;
      fallback.innerHTML = '<b>Structure lookup needs another identifier</b><small>Try a CAS number or paste a SMILES string for this compound.</small>';
    }
  }
}

async function downloadStructureSdf(event, url) {
  event.preventDefault();
  try {
    const response = await fetch(url, {headers:authHeaders(), credentials:'same-origin'});
    if (!response.ok) throw new Error((await response.json()).detail || 'Structure download failed');
    const blob = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = blob; link.download = 'molecular-structure.sdf'; link.click();
    setTimeout(()=>URL.revokeObjectURL(blob),10000);
  } catch (error) { toast(error.message); }
}

async function setupMolViewerClean(smiles, id, conformerUrl) {
  const host = document.getElementById('mol3d-'+id) || document.getElementById(id);
  if (!host) return null;
  const previous = window.moleculeViewers[id];
  if (previous) { try { previous.spin(false); previous.clear(); } catch (_) {} }
  host.innerHTML = '<div class="viewer-loading">Generating molecular coordinates…</div>';
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(), 65000);
  const current = Symbol(id);
  host.renderRequest = current;
  try {
    if (!window.$3Dmol) throw new Error('The bundled molecular viewer could not load.');
    const url = conformerUrl || '/api/live-structure/3d?smiles='+encodeURIComponent(smiles||'');
    const response = await fetch(url, {signal:controller.signal, credentials:'same-origin', headers:authHeaders()});
    if (!response.ok) {
      const error = await response.json().catch(()=>({}));
      throw new Error(error.detail || 'Structure generation did not complete.');
    }
    const sdf = await response.text();
    if (!sdf.includes('M  END')) throw new Error('The source returned invalid molecular coordinates.');
    if (!host.isConnected || host.renderRequest !== current) return null;
    host.innerHTML = '';
    const viewer = window.$3Dmol.createViewer(host, {backgroundColor:'#071421',antialias:true});
    const model = viewer.addModel(sdf, 'sdf');
    if (!model || !model.selectedAtoms({}).length) throw new Error('The structure has no atoms.');
    viewer.setStyle({},moleculeStyleObject('ball'));
    viewer.zoomTo();
    viewer.resize();
    viewer.render();
    window.moleculeViewers[id] = viewer;
    state.moleculeViewer = {viewer,style:'ball',spinning:false};
    host.dataset.ready = 'true';
    const observer = new ResizeObserver(()=>{if(host.isConnected){viewer.resize();viewer.render();}else observer.disconnect();});
    observer.observe(host);
    return viewer;
  } catch (error) {
    if (!host.isConnected || host.renderRequest !== current) return null;
    host.innerHTML = '<div class="viewer-fallback"><b>3D structure could not be loaded</b><small>'+esc(error.name==='AbortError'?'Coordinate generation timed out. Try again.':error.message)+'</small><button class="btn ghost viewer-retry">Retry structure</button></div>';
    host.querySelector('.viewer-retry').onclick = ()=>setupMolViewerClean(smiles,id,conformerUrl);
    return null;
  } finally { clearTimeout(timer); }
}

function fitAuthCard(){
  if(!document.body.classList.contains('auth-open')) return;
  const card=document.querySelector('.auth-card');
  if(!card) return;
  const scale=Math.min(state.appZoom||1.5,(window.innerWidth-32)/card.offsetWidth,(window.innerHeight-32)/card.offsetHeight);
  card.style.setProperty('--auth-fit',Math.max(0.1,scale));
}
window.addEventListener('resize',fitAuthCard);
window.setupMolViewerClean=setupMolViewerClean;
window.recoverStructureImage=recoverStructureImage;
window.downloadStructureSdf=downloadStructureSdf;
