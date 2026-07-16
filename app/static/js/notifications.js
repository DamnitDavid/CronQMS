// Toggle the shellbar notification bell dropdown. The panel's contents are
// loaded and refreshed by HTMX (see #notifMenu in base_admin.html); this only
// handles open/close.
document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("notifToggle");
    var menu = document.getElementById("notifMenu");
    if (!toggle || !menu) {
        return;
    }

    toggle.addEventListener("click", function (e) {
        e.stopPropagation();
        menu.hidden = !menu.hidden;
    });

    // Close when clicking anywhere outside the bell/panel.
    document.addEventListener("click", function (e) {
        if (!menu.hidden && !menu.contains(e.target) && !toggle.contains(e.target)) {
            menu.hidden = true;
        }
    });

    // Close on Escape.
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !menu.hidden) {
            menu.hidden = true;
        }
    });
});
