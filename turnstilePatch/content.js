// Turnstile Patch - hide automation signals and assist Turnstile checks.
// Runs at document_start (MV3 MAIN world) before page scripts when possible.

(function () {
  "use strict";

  // 1. Hide navigator.webdriver
  try {
    Object.defineProperty(navigator, "webdriver", {
      get: function () {
        return false;
      },
      configurable: true,
    });
  } catch (e) {}

  // 2. Strip chrome.runtime automation hooks when present
  try {
    if (window.chrome && window.chrome.runtime) {
      try {
        delete window.chrome.runtime.onConnect;
      } catch (e) {}
      try {
        delete window.chrome.runtime.onMessage;
      } catch (e) {}
    }
  } catch (e) {}

  // 3. Normalize permissions.query for notifications
  try {
    var origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = function (params) {
      if (params && params.name === "notifications") {
        return Promise.resolve({ state: Notification.permission });
      }
      return origQuery(params);
    };
  } catch (e) {}

  // 4. Present a non-empty plugins list
  try {
    Object.defineProperty(navigator, "plugins", {
      get: function () {
        return [1, 2, 3, 4, 5];
      },
      configurable: true,
    });
  } catch (e) {}

  // 5. Languages
  try {
    Object.defineProperty(navigator, "languages", {
      get: function () {
        return ["en-US", "en"];
      },
      configurable: true,
    });
  } catch (e) {}

  // 6. Poll for Turnstile and attempt checkbox click when same-origin
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoClickTurnstile);
  } else {
    autoClickTurnstile();
  }

  function autoClickTurnstile() {
    var checkCount = 0;
    var maxChecks = 100; // ~50s at 500ms
    var timer = setInterval(function () {
      checkCount++;
      if (checkCount > maxChecks) {
        clearInterval(timer);
        return;
      }
      try {
        var iframes = document.querySelectorAll(
          'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]'
        );
        for (var i = 0; i < iframes.length; i++) {
          var iframe = iframes[i];
          try {
            var body =
              iframe.contentDocument ||
              (iframe.contentWindow && iframe.contentWindow.document);
            if (!body) {
              continue;
            }
            var checkbox = body.querySelector(
              'input[type="checkbox"], .mark, #cf-chl-widget-nomu1_resp'
            );
            if (checkbox && !checkbox.checked) {
              checkbox.click();
            }
          } catch (e) {
            // cross-origin: best-effort postMessage
            try {
              if (iframe.contentWindow) {
                iframe.contentWindow.postMessage(
                  { type: "turnstile-auto-click" },
                  "*"
                );
              }
            } catch (e2) {}
          }
        }

        if (
          window.turnstile &&
          typeof window.turnstile.getResponse === "function"
        ) {
          var resp = window.turnstile.getResponse();
          if (resp && resp.length > 0) {
            clearInterval(timer);
          }
        }
      } catch (e) {}
    }, 500);
  }
})();
