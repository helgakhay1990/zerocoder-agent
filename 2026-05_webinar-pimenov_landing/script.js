document.addEventListener('DOMContentLoaded', () => {

    document.querySelectorAll('.glossary__item').forEach((item) => {
        const btn = item.querySelector('.glossary__term');
        btn.addEventListener('click', () => {
            const isOpen = item.classList.toggle('is-open');
            btn.setAttribute('aria-expanded', String(isOpen));
        });
    });

});
