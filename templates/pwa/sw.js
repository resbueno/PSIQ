/* Service worker do PSIQ. Proposital: NAO guarda nada em cache nem intercepta requisicoes,
   para que nenhum dado de paciente fique no aparelho (sem modo offline). Serve so para notificacoes push. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (event) {
  var dados = {};
  try { dados = event.data ? event.data.json() : {}; } catch (e) { dados = {}; }
  event.waitUntil(self.registration.showNotification(dados.title || 'Aviso', {
    body: dados.body || '',
    icon: '{% load static %}{% static "pwa/icone.svg" %}',
    data: { url: dados.url || '/portal/' }
  }));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  event.waitUntil(self.clients.openWindow(event.notification.data.url));
});
