/* Service Worker for 菇勇者 web push handling */

self.addEventListener('push', function(event) {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = { title: '提醒', body: event.data ? event.data.text() : '有新事件' };
  }

  const title = payload.title || '菇勇者提醒';
  const options = {
    body: payload.body || '',
    icon: '/icons/icon-192.png',
    badge: '/icons/badge-72.png',
    vibrate: [200, 100, 200],
    data: payload.data || {},
    renotify: true
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      // Try to focus an existing client
      for (const client of windowClients) {
        if (client.url && 'focus' in client) {
          // postMessage to client to play sound when focused
          try { client.postMessage({ type: 'play-sound' }); } catch (e) {}
          return client.focus();
        }
      }
      // otherwise open a new window
      if (clients.openWindow) return clients.openWindow('/');
    })
  );
});
