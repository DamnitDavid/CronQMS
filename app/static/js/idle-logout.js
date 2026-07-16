// Security auto-logout: sign the user out after a period of inactivity, with a
// countdown warning shortly beforehand so active users can stay signed in.
//
// The idle window mirrors the server's `session_timeout_minutes` setting (15).
// Logout clears the HttpOnly session cookie via the existing browser-logout
// endpoint, then redirects to the login page.
(function () {
    var IDLE_LIMIT_MS = 15 * 60 * 1000; // total inactivity before logout
    var WARN_BEFORE_MS = 60 * 1000;     // show the warning this long before logout
    var LOGOUT_URL = "/api/auth/browser-logout";
    var LOGIN_URL = "/login";

    var modal = document.getElementById("idleModal");
    if (!modal) {
        return;
    }
    var countdownEl = document.getElementById("idleCountdown");
    var stayBtn = document.getElementById("idleStay");
    var logoutBtn = document.getElementById("idleLogout");

    var warnTimer = null;
    var idleTimer = null;
    var countdownTimer = null;

    function clearTimers() {
        clearTimeout(warnTimer);
        clearTimeout(idleTimer);
        clearInterval(countdownTimer);
    }

    function doLogout() {
        clearTimers();
        fetch(LOGOUT_URL, { method: "POST", credentials: "same-origin" })
            .catch(function () { /* network error — still redirect */ })
            .then(function () { window.location = LOGIN_URL; });
    }

    function showWarning() {
        modal.hidden = false;
        var remaining = Math.round(WARN_BEFORE_MS / 1000);
        if (countdownEl) {
            countdownEl.textContent = remaining;
        }
        countdownTimer = setInterval(function () {
            remaining -= 1;
            if (countdownEl) {
                countdownEl.textContent = remaining;
            }
            if (remaining <= 0) {
                doLogout();
            }
        }, 1000);
    }

    function reset() {
        clearTimers();
        modal.hidden = true;
        warnTimer = setTimeout(showWarning, IDLE_LIMIT_MS - WARN_BEFORE_MS);
        idleTimer = setTimeout(doLogout, IDLE_LIMIT_MS);
    }

    // Any user activity resets the timer — but only while the warning is not
    // showing, so that the explicit "Stay signed in" choice is required once
    // the countdown has begun.
    ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "click"].forEach(function (evt) {
        document.addEventListener(evt, function () {
            if (modal.hidden) {
                reset();
            }
        }, { passive: true });
    });

    if (stayBtn) {
        stayBtn.addEventListener("click", reset);
    }
    if (logoutBtn) {
        logoutBtn.addEventListener("click", doLogout);
    }

    reset();
})();
