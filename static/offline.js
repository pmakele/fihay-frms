const DB='fihay-offline-v2', STORE='queue';
const PREPARE_PAGES=['/','/plots','/crops','/harvests','/sales','/expenses','/livestock','/inventory','/water','/incidents','/help','/admin/master-data'];
function openDb(){return new Promise((res,rej)=>{const r=indexedDB.open(DB,1);r.onupgradeneeded=()=>{if(!r.result.objectStoreNames.contains(STORE))r.result.createObjectStore(STORE,{keyPath:'id',autoIncrement:true});};r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error);});}
async function queueRecord(record){const db=await openDb();return new Promise((res,rej)=>{const tx=db.transaction(STORE,'readwrite');tx.objectStore(STORE).add(record);tx.oncomplete=res;tx.onerror=()=>rej(tx.error);});}
async function allQueued(){const db=await openDb();return new Promise((res,rej)=>{const r=db.transaction(STORE).objectStore(STORE).getAll();r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error);});}
async function removeQueued(id){const db=await openDb();return new Promise((res,rej)=>{const tx=db.transaction(STORE,'readwrite');tx.objectStore(STORE).delete(id);tx.oncomplete=res;tx.onerror=()=>rej(tx.error);});}
function showOffline(msg){const b=document.getElementById('offlineBanner');if(b){b.hidden=false;b.textContent=msg||'Offline mode: text-only records will queue on this device and sync when this farm account reconnects.';}}
function hideOffline(){const b=document.getElementById('offlineBanner');if(b)b.hidden=true;}
async function context(){try{const r=await fetch('/api/context',{cache:'no-store'});if(!r.ok)return null;return await r.json();}catch(e){return null;}}
async function syncQueue(){let rows=[];try{rows=await allQueued();}catch(e){return;} if(!rows.length)return;const c=await context();if(!c){showOffline();return;}let synced=0;for(const row of rows){if(String(row.userId)!==String(c.user_id)||String(row.farmId)!==String(c.farm_id)){showOffline('Offline records are waiting for a different farm/account. Sign back into that farm before syncing them.');continue;}try{const body=new URLSearchParams();Object.entries(row.fields).forEach(([k,v])=>body.append(k,v));const resp=await fetch(row.action,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},body,redirect:'follow'});if(resp.ok){await removeQueued(row.id);synced++;}else return;}catch(e){showOffline();return;}}if(synced){hideOffline();location.reload();}}
async function prepareOffline(){if(!navigator.onLine||!window.FIHAY_USER_ID)return;for(const u of PREPARE_PAGES){try{await fetch(u,{credentials:'same-origin'});}catch(e){break;}}}
window.addEventListener('online',()=>syncQueue());window.addEventListener('offline',()=>showOffline());
document.addEventListener('DOMContentLoaded',()=>{
  if(!navigator.onLine)showOffline(); else {syncQueue();setTimeout(prepareOffline,1200);}
  document.querySelectorAll('form[method="post"],form[method="POST"]').forEach(form=>form.addEventListener('submit',async ev=>{
    if(form.dataset.noOfflineQueue==='true')return;
    ev.preventDefault();const fd=new FormData(form);const files=[...form.querySelectorAll('input[type="file"]')].filter(x=>x.files&&x.files.length);
    try{const resp=await fetch(form.action,{method:'POST',body:fd,redirect:'follow'});if(!resp.ok)throw new Error('server');hideOffline();window.location.href=resp.url||window.location.href;return;}catch(e){}
    if(files.length){showOffline();alert('This record includes a file. Reconnect first, or remove the file, save the record offline, and attach the file later.');return;}
    if(!window.FIHAY_USER_ID||!window.FIHAY_FARM_ID){alert('You must be signed into a farm before an offline record can be queued.');return;}
    const fields={};fd.forEach((v,k)=>{if(typeof v==='string')fields[k]=v;});await queueRecord({action:form.action,fields,userId:window.FIHAY_USER_ID,farmId:window.FIHAY_FARM_ID,created:new Date().toISOString()});showOffline();alert('Saved on this device. It will sync automatically when this same farm account reconnects.');
  }));
});
