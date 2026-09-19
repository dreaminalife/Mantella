PROMPT_PROFILES_TAB = "Prompt Profiles"
LIVE_CONVERSATION_TAB = "Live Conversation"
BIO_EDITOR_TAB = "Bio Editor"
DOCUMENTATION_TAB = "Documentation"

TAB_REORDER_JS = r"""
(function () {
  if (window.__mantellaTabReorderInit) {
    return;
  }
  window.__mantellaTabReorderInit = true;
  const STORAGE_KEY = "mantella-tab-order";
  const DRAG_THRESHOLD_PX = 8;
  let applying = false;
  let setupTimer = null;
  let drag = null;

  function isMainTabNav(nav) {
    if (!nav || !nav.classList || !nav.classList.contains("tab-nav")) {
      return false;
    }
    const labels = buttonLabels(nav);
    return labels.indexOf("Game") !== -1
      || labels.indexOf("Bio Editor") !== -1
      || labels.indexOf("Documentation") !== -1;
  }

  function mainTabNavs() {
    return Array.prototype.filter.call(document.querySelectorAll(".tab-nav"), isMainTabNav);
  }

  function buttons(nav) {
    return Array.prototype.slice.call(nav.querySelectorAll(":scope > button"));
  }

  function buttonLabels(nav) {
    return buttons(nav).map(function (btn) {
      return (btn.textContent || "").trim();
    });
  }

  function readSavedOrder() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : null;
    } catch (e) {
      return null;
    }
  }

  function saveOrder(order) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
    } catch (e) {}
    try {
      fetch("/tab-order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order: order })
      }).catch(function () {});
    } catch (e) {}
  }

  function applyOrder(nav) {
    const order = readSavedOrder();
    if (!order || !order.length) {
      return;
    }
    const current = buttons(nav);
    const byLabel = {};
    current.forEach(function (btn) {
      const label = (btn.textContent || "").trim();
      if (!byLabel[label]) {
        byLabel[label] = [];
      }
      byLabel[label].push(btn);
    });
    applying = true;
    const placed = [];
    order.forEach(function (name) {
      const list = byLabel[name];
      if (!list || !list.length) {
        return;
      }
      const btn = list.shift();
      nav.appendChild(btn);
      placed.push(btn);
    });
    current.forEach(function (btn) {
      if (placed.indexOf(btn) === -1) {
        nav.appendChild(btn);
      }
    });
    applying = false;
  }

  function bindNav(nav) {
    if (nav.dataset.mantellaReorder === "1") {
      return;
    }
    nav.dataset.mantellaReorder = "1";
    nav.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) {
        return;
      }
      const btn = e.target.closest("button");
      if (!btn || btn.parentElement !== nav) {
        return;
      }
      drag = {
        nav: nav,
        btn: btn,
        startX: e.clientX,
        started: false
      };
    });
  }

  function onPointerMove(e) {
    if (!drag) {
      return;
    }
    if (!drag.started) {
      if (Math.abs(e.clientX - drag.startX) < DRAG_THRESHOLD_PX) {
        return;
      }
      drag.started = true;
      drag.btn.classList.add("mantella-tab-dragging");
    }
    e.preventDefault();
    const siblings = buttons(drag.nav).filter(function (btn) {
      return btn !== drag.btn;
    });
    let target = null;
    let placeAfter = false;
    for (let i = 0; i < siblings.length; i++) {
      const rect = siblings[i].getBoundingClientRect();
      const mid = rect.left + rect.width / 2;
      if (e.clientX < mid) {
        target = siblings[i];
        placeAfter = false;
        break;
      }
      target = siblings[i];
      placeAfter = true;
    }
    if (!target) {
      return;
    }
    if (placeAfter) {
      if (target.nextSibling !== drag.btn) {
        target.after(drag.btn);
      }
    } else if (target.previousSibling !== drag.btn) {
      target.before(drag.btn);
    }
  }

  function onPointerUp() {
    if (!drag) {
      return;
    }
    if (drag.started) {
      drag.btn.classList.remove("mantella-tab-dragging");
      saveOrder(buttonLabels(drag.nav));
    }
    drag = null;
  }

  function setup() {
    mainTabNavs().forEach(function (nav) {
      bindNav(nav);
      if (!drag) {
        applyOrder(nav);
      }
    });
  }

  function scheduleSetup() {
    if (applying || (drag && drag.started)) {
      return;
    }
    if (setupTimer) {
      clearTimeout(setupTimer);
    }
    setupTimer = setTimeout(setup, 40);
  }

  document.addEventListener("pointermove", onPointerMove, { passive: false });
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);

  const observer = new MutationObserver(scheduleSetup);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
"""


def default_tab_names(base_groups) -> list[str]:
    names: list[str] = []
    for group in base_groups:
        if group.is_hidden:
            continue
        names.append(group.name)
        if group.name == PROMPT_PROFILES_TAB:
            names.append(LIVE_CONVERSATION_TAB)
    names.append(BIO_EDITOR_TAB)
    names.append(DOCUMENTATION_TAB)
    return names


def merge_tab_order(defaults: list[str], saved: list[str]) -> list[str]:
    default_set = set(defaults)
    ordered = [name for name in saved if name in default_set]
    seen = set(ordered)
    for name in defaults:
        if name not in seen:
            ordered.append(name)
    return ordered
