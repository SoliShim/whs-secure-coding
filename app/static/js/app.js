(function () {
  "use strict";

  const navToggle = document.querySelector("[data-nav-toggle]");
  const navMenu = document.querySelector("[data-nav-menu]");

  function closeNavigation() {
    if (!navToggle || !navMenu) return;
    navToggle.setAttribute("aria-expanded", "false");
    navMenu.classList.remove("is-open");
  }

  if (navToggle && navMenu) {
    navToggle.addEventListener("click", function () {
      const willOpen = navToggle.getAttribute("aria-expanded") !== "true";
      navToggle.setAttribute("aria-expanded", String(willOpen));
      navMenu.classList.toggle("is-open", willOpen);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && navMenu.classList.contains("is-open")) {
        closeNavigation();
        navToggle.focus();
      }
    });

    document.addEventListener("click", function (event) {
      if (!navMenu.classList.contains("is-open")) return;
      if (!navMenu.contains(event.target) && !navToggle.contains(event.target)) {
        closeNavigation();
      }
    });
  }

  document.querySelectorAll("[data-dismiss]").forEach(function (button) {
    button.addEventListener("click", function () {
      const alert = button.closest(".alert");
      if (alert) alert.remove();
    });
  });

  document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      const input = document.getElementById(button.dataset.passwordToggle || "");
      if (!input || (input.type !== "password" && input.type !== "text")) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "숨기기" : "보기";
      button.setAttribute("aria-label", show ? "비밀번호 숨기기" : "비밀번호 표시");
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      const prompt = form.dataset.confirm || "이 작업을 계속할까요?";
      if (!window.confirm(prompt)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-image-input]").forEach(function (input) {
    input.addEventListener("change", function () {
      const file = input.files && input.files[0];
      if (!file) return;
      if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
        input.setCustomValidity("JPG, PNG, WebP 형식의 이미지만 선택해 주세요.");
        input.reportValidity();
        input.value = "";
        return;
      }
      input.setCustomValidity("");
    });
  });

  function secureRandomKey() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const values = new Uint8Array(16);
    crypto.getRandomValues(values);
    values[6] = (values[6] & 0x0f) | 0x40;
    values[8] = (values[8] & 0x3f) | 0x80;
    return Array.from(values, function (value) {
      return value.toString(16).padStart(2, "0");
    }).join("");
  }

  document.querySelectorAll("[data-idempotency-key]").forEach(function (input) {
    if (!input.value && window.crypto && typeof crypto.getRandomValues === "function") {
      input.value = secureRandomKey();
    }
  });

})();
