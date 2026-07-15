/*
 * AlertImageEditor — a small, dependency-free image editor for alert photos.
 *
 * Supports: choose an image, rotate 90°, crop (drag a rectangle), and annotate
 * (drag to draw red freehand marks to circle a defect). The edited result is
 * flattened to a PNG on a <canvas> and POSTed to the alert's images endpoint.
 */
(function () {
    "use strict";

    var overlay, canvas, ctx, committed, fileInput, saveBtn, modeLabel, posLabel;
    var state = {
        alertId: null,
        position: null,
        mode: "view", // view | crop | annotate
        dragging: false,
        start: null,
        last: null,
    };

    function el(id) { return document.getElementById(id); }

    function init() {
        overlay = el("imgEditorOverlay");
        canvas = el("imgEditorCanvas");
        fileInput = el("imgEditorFile");
        saveBtn = el("imgEditorSave");
        modeLabel = el("imgEditorMode");
        posLabel = el("imgEditorPos");
        if (!overlay || !canvas) return;
        ctx = canvas.getContext("2d");
        committed = document.createElement("canvas");

        fileInput.addEventListener("change", onFile);
        overlay.querySelectorAll("[data-act]").forEach(function (btn) {
            btn.addEventListener("click", function () { onAction(btn.getAttribute("data-act")); });
        });
        canvas.addEventListener("mousedown", onDown);
        canvas.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
    }

    function setMode(m) {
        state.mode = m;
        if (modeLabel) modeLabel.textContent = "mode: " + m;
    }

    function hasImage() { return committed.width > 0 && committed.height > 0; }

    function redraw(overlayRect) {
        canvas.width = committed.width;
        canvas.height = committed.height;
        ctx.drawImage(committed, 0, 0);
        if (overlayRect) {
            ctx.save();
            ctx.strokeStyle = "#1d4ed8";
            ctx.lineWidth = Math.max(2, committed.width / 300);
            ctx.setLineDash([6, 4]);
            ctx.strokeRect(overlayRect.x, overlayRect.y, overlayRect.w, overlayRect.h);
            ctx.restore();
        }
    }

    function loadInto(img) {
        committed.width = img.width || img.naturalWidth;
        committed.height = img.height || img.naturalHeight;
        committed.getContext("2d").drawImage(img, 0, 0);
        setMode("view");
        redraw();
        if (saveBtn) saveBtn.disabled = false;
    }

    function onFile(e) {
        var f = e.target.files && e.target.files[0];
        if (!f) return;
        var img = new Image();
        img.onload = function () { loadInto(img); URL.revokeObjectURL(img.src); };
        img.src = URL.createObjectURL(f);
    }

    function rotate90() {
        if (!hasImage()) return;
        var out = document.createElement("canvas");
        out.width = committed.height;
        out.height = committed.width;
        var octx = out.getContext("2d");
        octx.translate(out.width, 0);
        octx.rotate(Math.PI / 2);
        octx.drawImage(committed, 0, 0);
        committed = out;
        redraw();
    }

    function toImageCoords(e) {
        var rect = canvas.getBoundingClientRect();
        var sx = committed.width / rect.width;
        var sy = committed.height / rect.height;
        return { x: (e.clientX - rect.left) * sx, y: (e.clientY - rect.top) * sy };
    }

    function onDown(e) {
        if (!hasImage() || state.mode === "view") return;
        state.dragging = true;
        state.start = toImageCoords(e);
        state.last = state.start;
    }

    function onMove(e) {
        if (!state.dragging) return;
        var p = toImageCoords(e);
        if (state.mode === "crop") {
            redraw({ x: Math.min(state.start.x, p.x), y: Math.min(state.start.y, p.y),
                     w: Math.abs(p.x - state.start.x), h: Math.abs(p.y - state.start.y) });
        } else if (state.mode === "annotate") {
            var c = committed.getContext("2d");
            c.strokeStyle = "#e11d48";
            c.lineWidth = Math.max(3, committed.width / 200);
            c.lineCap = "round";
            c.beginPath();
            c.moveTo(state.last.x, state.last.y);
            c.lineTo(p.x, p.y);
            c.stroke();
            state.last = p;
            redraw();
        }
    }

    function onUp(e) {
        if (!state.dragging) return;
        state.dragging = false;
        if (state.mode === "crop") {
            var p = toImageCoords(e);
            var x = Math.min(state.start.x, p.x), y = Math.min(state.start.y, p.y);
            var w = Math.abs(p.x - state.start.x), h = Math.abs(p.y - state.start.y);
            if (w > 5 && h > 5) {
                var out = document.createElement("canvas");
                out.width = w; out.height = h;
                out.getContext("2d").drawImage(committed, x, y, w, h, 0, 0, w, h);
                committed = out;
                setMode("view");
            }
            redraw();
        }
    }

    function onAction(act) {
        if (act === "rotate") { rotate90(); }
        else if (act === "crop") { setMode(state.mode === "crop" ? "view" : "crop"); }
        else if (act === "annotate") { setMode(state.mode === "annotate" ? "view" : "annotate"); }
        else if (act === "reset") { fileInput.value = ""; committed.width = 0; committed.height = 0; redraw(); if (saveBtn) saveBtn.disabled = true; }
        else if (act === "save") { save(); }
    }

    function save() {
        if (!hasImage()) return;
        saveBtn.disabled = true;
        committed.toBlob(function (blob) {
            var fd = new FormData();
            fd.append("position", String(state.position));
            fd.append("file", blob, "alert-photo-" + state.position + ".png");
            fetch("/admin/alerts/" + state.alertId + "/images", { method: "POST", body: fd })
                .then(function (r) {
                    if (!r.ok) throw new Error("upload failed");
                    window.location.reload();
                })
                .catch(function () { alert("Could not save the photo."); saveBtn.disabled = false; });
        }, "image/png");
    }

    var api = {
        open: function (alertId, position) {
            if (!overlay) init();
            state.alertId = alertId;
            state.position = position;
            committed.width = 0; committed.height = 0;
            fileInput.value = "";
            setMode("view");
            if (posLabel) posLabel.textContent = position;
            if (saveBtn) saveBtn.disabled = true;
            if (ctx) { canvas.width = 0; canvas.height = 0; }
            overlay.hidden = false;
        },
        close: function () { if (overlay) overlay.hidden = true; },
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
    window.AlertImageEditor = api;
})();
