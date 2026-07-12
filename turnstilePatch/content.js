// Turnstile Patch - hide automation signals, assist Turnstile
(function () {
  "use strict";
  try {
    Object.defineProperty(navigator, "webdriver", {
      get: function () { return false; },
      configurable: true,
    });
  } catch (e) {}
  try {
    if (window.chrome && window.chrome.runtime) {
      try { delete window.chrome.runtime.onConnect; } catch (e) {}
      try { delete window.chrome.runtime.onMessage; } catch (e) {}
    }
  } catch (e) {}
  try {
    var origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = function (params) {
      if (params && params.name === "notifications") {
        return Promise.resolve({ state: Notification.permission });
      }
      return origQuery(params);
    };
  } catch (e) {}
  try {
    Object.defineProperty(navigator, "plugins", {
      get: function () { return [1, 2, 3, 4, 5]; },
      configurable: true,
    });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, "languages", {
      get: function () { return ["en-US", "en"]; },
      configurable: true,
    });
  } catch (e) {}
})();
