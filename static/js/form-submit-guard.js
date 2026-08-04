/* Prevent repeated navigation-form submissions while a request is in flight. */
(function () {
  "use strict";

  function resetForm(form) {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
    form.querySelectorAll("[data-submit-guard-disabled]").forEach(function (button) {
      button.disabled = false;
      button.removeAttribute("data-submit-guard-disabled");
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;
    if ((form.method || "get").toLowerCase() !== "post") return;
    if (form.hasAttribute("data-allow-resubmit")) return;

    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }

    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");

    // Delay disabling until the submit event has captured the clicked
    // submitter's name/value for the outgoing request.
    window.setTimeout(function () {
      form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (button) {
        if (!button.disabled) {
          button.disabled = true;
          button.setAttribute("data-submit-guard-disabled", "true");
        }
      });
    }, 0);
  });

  // Browsers may restore a page from the back-forward cache with controls in
  // their previous state. Re-enable them when that page becomes visible.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-submitting]").forEach(resetForm);
  });
})();
