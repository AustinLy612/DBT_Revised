(function () {
  "use strict";

  var storageKey = "dbt-theme";
  var root = document.documentElement;
  var systemDark = window.matchMedia("(prefers-color-scheme: dark)");

  function savedTheme() {
    try {
      var value = window.localStorage.getItem(storageKey);
      return value === "light" || value === "dark" ? value : null;
    } catch (_error) {
      return null;
    }
  }

  function applyTheme(theme) {
    root.dataset.theme = theme;
    var button = document.querySelector(".dbt-theme-toggle");
    if (!button) return;
    var english = root.lang.toLowerCase().startsWith("en");
    var next = theme === "dark" ? (english ? "Light mode" : "浅色模式") : (english ? "Dark mode" : "深色模式");
    var action = english ? "Switch to " + next.toLowerCase() : "切换到" + next;
    button.setAttribute("aria-label", action);
    button.title = action;
    button.querySelector(".dbt-theme-label").textContent = next;
    button.querySelector(".dbt-theme-icon").textContent = theme === "dark" ? "☀" : "☾";
  }

  applyTheme(savedTheme() || (systemDark.matches ? "dark" : "light"));

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.querySelector(".dbt-theme-toggle");
    if (!button) return;
    applyTheme(root.dataset.theme);
    button.addEventListener("click", function () {
      var next = root.dataset.theme === "dark" ? "light" : "dark";
      try {
        window.localStorage.setItem(storageKey, next);
      } catch (_error) {
        // Theme still changes for this page when storage is unavailable.
      }
      applyTheme(next);
    });
  });

  function followSystem(event) {
    if (!savedTheme()) applyTheme(event.matches ? "dark" : "light");
  }
  if (systemDark.addEventListener) {
    systemDark.addEventListener("change", followSystem);
  } else if (systemDark.addListener) {
    systemDark.addListener(followSystem);
  }
})();
