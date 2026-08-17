/* Общий JS веб-портала:
   - тема (dark/light) для шаблонов, которые её поддерживают (rustdesk);
   - глобальный редирект на /login при ошибке авторизации (401 от /api/*);
   - переключатель языка и применение переводов (см. static/js/i18n.js). */
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
            btn.title = t('theme_toggle');
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

    /* ---- Редирект на /login при ошибке авторизации / истечении сессии ---- */
    function installAuthRedirect() {
        if (!window.fetch) return;
        var origFetch = window.fetch;
        window.fetch = function () {
            var args = arguments;
            return origFetch.apply(this, args).then(function (resp) {
                try {
                    var url = String((args[0] && args[0].url) || args[0] || '');
                    if (resp.status === 401 && url.indexOf('/api/') !== -1 &&
                        window.location.pathname !== '/login') {
                        window.location.href = '/login';
                    }
                } catch (e) {}
                return resp;
            });
        };
    }

    /* ---- i18n ---- */
    function t(key, params) {
        var lang = window.I18N ? window.I18N.lang : 'ru';
        var dict = (window.I18N_DICT && window.I18N_DICT[lang]) || {};
        var s = dict[key];
        if (s === undefined) s = (window.I18N_DICT && window.I18N_DICT.ru && window.I18N_DICT.ru[key]) !== undefined
            ? window.I18N_DICT.ru[key] : key;
        if (params) Object.keys(params).forEach(function (k) { s = s.split('{' + k + '}').join(params[k]); });
        return s;
    }
    window.t = t;

    function applyI18n(root) {
        var scope = root || document;
        scope.querySelectorAll('[data-i18n]').forEach(function (el) { el.textContent = t(el.getAttribute('data-i18n')); });
        scope.querySelectorAll('[data-i18n-ph]').forEach(function (el) { el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph'))); });
        scope.querySelectorAll('[data-i18n-title]').forEach(function (el) { el.setAttribute('title', t(el.getAttribute('data-i18n-title'))); });
        scope.querySelectorAll('[data-i18n-html]').forEach(function (el) { el.innerHTML = t(el.getAttribute('data-i18n-html')); });
    }
    window.applyI18n = applyI18n;

    function currentLang() {
        try {
            var stored = localStorage.getItem('ui_lang');
            if (stored === 'ru' || stored === 'en') return stored;
        } catch (e) {}
        return ((navigator.language || 'ru').toLowerCase().indexOf('ru') === 0) ? 'ru' : 'en';
    }
    function setLang(lang) {
        try { localStorage.setItem('ui_lang', lang); } catch (e) {}
        window.location.reload();
    }
    window.setLang = setLang;

    // Язык определяется сразу: инлайн-скрипты страниц строят константы
    // (CONN_TYPES, RULE_NAMES...) через t() ещё до DOMContentLoaded.
    if (window.I18N) window.I18N.lang = currentLang();

    function injectLangSwitch() {
        document.querySelectorAll('[data-lang-switch]').forEach(function (host) {
            var sel = document.createElement('select');
            sel.className = 'lang-switch';
            sel.setAttribute('data-i18n-title', 'language');
            ['ru', 'en'].forEach(function (l) {
                var op = document.createElement('option');
                op.value = l; op.textContent = l.toUpperCase();
                if (l === currentLang()) op.selected = true;
                sel.appendChild(op);
            });
            sel.addEventListener('change', function () { setLang(sel.value); });
            host.appendChild(sel);
        });
    }

    function init() {
        document.documentElement.setAttribute('lang', currentLang());
        installAuthRedirect();
        // Переключатель темы показываем только если шаблон поддерживает темы.
        document.querySelectorAll('[data-action="toggle-theme"]').forEach(function (btn) {
            if (!supportsTheme()) { btn.style.display = 'none'; return; }
            btn.addEventListener('click', toggleTheme);
        });
        updateThemeIcons(currentTheme());
        injectLangSwitch();
        applyI18n();
    }

    window.UI = { currentTheme: currentTheme, applyTheme: applyTheme, toggleTheme: toggleTheme };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
