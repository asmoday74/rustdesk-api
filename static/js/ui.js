/* Менеджер темы (dark/light) для шаблонов, которые её поддерживают (rustdesk).
   Тема хранится в localStorage и применяется мгновенно.
   Дизайн выбирается сервером (env TEMPLATE) — здесь не переключается. */
(function () {
    'use strict';

    var THEME_KEY = 'ui_theme'; // 'dark' | 'light'

    function supportsTheme() {
        return document.documentElement.getAttribute('data-design') !== 'sve';
    }
    function currentTheme() {
        return document.documentElement.getAttribute('data-theme') || 'dark';
    }
    function updateThemeIcons(theme) {
        document.querySelectorAll('[data-action="toggle-theme"]').forEach(function (btn) {
            var icon = btn.querySelector('i');
            if (icon) icon.className = (theme === 'dark') ? 'fas fa-sun' : 'fas fa-moon';
            btn.title = (theme === 'dark') ? 'Светлая схема' : 'Тёмная схема';
        });
    }
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
        updateThemeIcons(theme);
    }
    function toggleTheme() {
        applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    }
    function init() {
        // Переключатель темы показываем только если шаблон поддерживает темы.
        document.querySelectorAll('[data-action="toggle-theme"]').forEach(function (btn) {
            if (!supportsTheme()) { btn.style.display = 'none'; return; }
            btn.addEventListener('click', toggleTheme);
        });
        updateThemeIcons(currentTheme());
    }

    window.UI = { currentTheme: currentTheme, applyTheme: applyTheme, toggleTheme: toggleTheme };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
