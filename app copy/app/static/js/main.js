document.addEventListener("DOMContentLoaded", () => {
    const forms = document.querySelectorAll("form[method='post']");
    if (!forms) {
        return;
    }

    forms.forEach((form) => {
        form.addEventListener("submit", (event) => {
            const invalid = Array.from(form.elements).some((field) => !field.checkValidity && !field.checkValidity());
            if (invalid) {
                event.preventDefault();
            }
        });
    });
});
