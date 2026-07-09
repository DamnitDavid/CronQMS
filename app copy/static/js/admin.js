document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.querySelector("#themeToggle");
    if (!toggle) {
        return;
    }

    toggle.addEventListener("click", () => {
        document.documentElement.classList.toggle("dark-mode");
        if (document.documentElement.classList.contains("dark-mode")) {
            toggle.textContent = "Light Mode";
        } else {
            toggle.textContent = "Dark Mode";
        }
    });
});
