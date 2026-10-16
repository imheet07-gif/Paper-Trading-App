// Minimal service worker so browsers offer "install". It deliberately caches
// nothing: prices and balances must never be served stale from a cache.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
