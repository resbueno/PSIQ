(function () {
  if (!('serviceWorker' in navigator)) { return; }
  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});

  var botao = document.getElementById('ativar-push');
  if (!botao || !('PushManager' in window)) { if (botao) { botao.hidden = true; } return; }

  function chaveParaBytes(base64) {
    var preenchida = (base64 + '='.repeat((4 - base64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    var bruto = atob(preenchida);
    return Uint8Array.from(bruto, function (c) { return c.charCodeAt(0); });
  }

  botao.addEventListener('click', function () {
    Notification.requestPermission().then(function (permissao) {
      if (permissao !== 'granted') { return; }
      return navigator.serviceWorker.ready.then(function (registro) {
        return registro.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: chaveParaBytes(botao.dataset.chave)
        });
      }).then(function (assinatura) {
        var token = document.querySelector('meta[name="csrf"]');
        return fetch(botao.dataset.url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token ? token.content : '' },
          credentials: 'same-origin',
          body: JSON.stringify(assinatura.toJSON())
        });
      }).then(function () { botao.textContent = 'Notificações ativadas'; botao.disabled = true; });
    });
  });
})();
