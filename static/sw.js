const CACHE='fihay-frms-v2-hosted-1';
const STATIC=['/static/style.css','/static/offline.js','/manifest.webmanifest'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(c=>c.addAll(STATIC)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET')return;
  const url=new URL(event.request.url);if(url.origin!==location.origin)return;
  if(url.pathname.startsWith('/uploads/')||url.pathname==='/login'||url.pathname==='/register'||url.pathname.startsWith('/api/'))return;
  event.respondWith(fetch(event.request).then(resp=>{
    if(resp.ok){const copy=resp.clone();caches.open(CACHE).then(c=>c.put(event.request,copy));}
    return resp;
  }).catch(()=>caches.match(event.request).then(r=>r||caches.match('/'))));
});
self.addEventListener('message',event=>{if(event.data&&event.data.type==='CLEAR_CACHE'){event.waitUntil(caches.keys().then(keys=>Promise.all(keys.map(k=>caches.delete(k)))));}});
