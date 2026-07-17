document.addEventListener("DOMContentLoaded", () => {
    const root = document.documentElement;
    const STORAGE_KEY = "cronqms-theme";

    const applyTheme = (theme) => {
        const dark = theme === "dark";
        root.classList.toggle("dark-mode", dark);
        if (dark) {
            root.setAttribute("data-theme", "dark");
        } else {
            root.removeAttribute("data-theme");
        }
    };

    // The pre-paint snippet in base.html has already applied the saved theme;
    // just keep localStorage and the DOM in sync from here.
    const toggle = document.querySelector("#themeToggle");
    if (!toggle) {
        return;
    }

    toggle.addEventListener("click", () => {
        const next = root.classList.contains("dark-mode") ? "light" : "dark";
        applyTheme(next);
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch (e) {
            /* localStorage unavailable */
        }
    });

    // Cmd/Ctrl+K jumps to the shellbar search, matching its "⌘K" hint.
    const shellSearch = document.querySelector("#shellSearch");
    if (shellSearch) {
        document.addEventListener("keydown", (event) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
                event.preventDefault();
                shellSearch.focus();
                shellSearch.select();
            }
        });
    }

    // Inline error banner for htmx swap failures (e.g. the events table
    // refresh) — keeps the last good results visible instead of blanking them.
    const htmxErrorBanner = (event) => {
        const target = event.detail && event.detail.target;
        const pane = target && target.closest(".events-pane");
        if (!pane) {
            return;
        }
        let banner = pane.querySelector(".banner-err");
        if (!banner) {
            banner = document.createElement("div");
            banner.className = "banner-err";
            banner.innerHTML =
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' +
                "<div><strong>Couldn’t refresh the table.</strong> Your filters were kept — the last results are still shown below.</div>";
            pane.prepend(banner);
        }
    };
    document.body.addEventListener("htmx:responseError", htmxErrorBanner);
    document.body.addEventListener("htmx:sendError", htmxErrorBanner);
    document.body.addEventListener("htmx:beforeRequest", (event) => {
        const target = event.detail && event.detail.target;
        const pane = target && target.closest(".events-pane");
        const banner = pane && pane.querySelector(".banner-err");
        if (banner) {
            banner.remove();
        }
    });
});
