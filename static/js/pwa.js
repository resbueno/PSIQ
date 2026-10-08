(function () {
  // Selecao que envia o formulario sozinha (substitui onchange inline, bloqueado pela CSP).
  document.querySelectorAll('select[data-autosubmit]').forEach(function (campo) {
    campo.addEventListener('change', function () { campo.form.submit(); });
  });

  // Marca no menu a pagina atual: o link de maior caminho que prefixa a URL (o Painel so vale na raiz exata).
  document.querySelectorAll('.lateral nav, .portal-topo nav').forEach(function (menu) {
    var atual = location.pathname, melhor = null, tamanho = -1;
    menu.querySelectorAll('a[href]').forEach(function (link) {
      var caminho = new URL(link.href, location.href).pathname;
      var base = caminho.charAt(caminho.length - 1) === '/' ? caminho : caminho + '/';
      var confere = link.hasAttribute('data-exato') ? atual === caminho : (atual === caminho || atual.indexOf(base) === 0);
      if (confere && caminho.length > tamanho) { melhor = link; tamanho = caminho.length; }
    });
    if (melhor) { melhor.setAttribute('aria-current', 'page'); }
  });

  // Menu recolhivel no celular. Sem JavaScript o menu fica sempre aberto.
  var lateral = document.querySelector('.lateral');
  var alternar = lateral && lateral.querySelector('.menu-toggle');
  if (alternar) {
    document.documentElement.classList.add('js');
    alternar.addEventListener('click', function () {
      var aberto = lateral.classList.toggle('aberta');
      alternar.setAttribute('aria-expanded', aberto ? 'true' : 'false');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && lateral.classList.contains('aberta')) {
        lateral.classList.remove('aberta');
        alternar.setAttribute('aria-expanded', 'false');
        alternar.focus();
      }
    });
  }

  if (!('serviceWorker' in navigator)) { return; }
  var meta = document.querySelector('meta[name="sw"]');
  if (!meta) { return; }
  // O endereco vem do servidor, que conhece o prefixo da instalacao (ex.: /meupsiq/).
  navigator.serviceWorker.register(meta.content, { scope: meta.content.replace(/sw\.js$/, '') }).catch(function () {});

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
