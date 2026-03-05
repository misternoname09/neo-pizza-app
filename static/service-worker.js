const CACHE_NAME = 'neo-pizza-v1';
const urlsToCache = [
    '/',
    '/static/style.css',
    '/static/manifest.json',
    '/offline.html'  // À créer
];

// Installation : mise en cache des ressources
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('Cache ouvert');
                return cache.addAll(urlsToCache);
            })
    );
    self.skipWaiting();
});

// Activation : nettoyage des anciens caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
});

// Interception des requêtes
self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request)
            .then(response => {
                // Retourne la ressource du cache si trouvée
                if (response) {
                    return response;
                }
                
                // Sinon, va la chercher sur le réseau
                return fetch(event.request).then(
                    networkResponse => {
                        // Vérifie si la réponse est valide
                        if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== 'basic') {
                            return networkResponse;
                        }
                        
                        // Met en cache la nouvelle ressource
                        const responseToCache = networkResponse.clone();
                        caches.open(CACHE_NAME)
                            .then(cache => {
                                cache.put(event.request, responseToCache);
                            });
                        
                        return networkResponse;
                    }
                ).catch(() => {
                    // En cas d'échec réseau, retourne la page hors ligne
                    if (event.request.mode === 'navigate') {
                        return caches.match('/offline.html');
                    }
                });
            })
    );
});