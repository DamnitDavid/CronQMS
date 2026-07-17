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
});
