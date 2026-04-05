/* AO Hunter - Client JS */

// --- Dark mode ---
(function() {
    const toggle = document.getElementById('darkModeToggle');
    if (!toggle) return;
    const iconSun = toggle.querySelector('.icon-sun');
    const iconMoon = toggle.querySelector('.icon-moon');
    const saved = localStorage.getItem('ao-theme');

    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('ao-theme', theme);
        if (iconSun && iconMoon) {
            iconSun.style.display = theme === 'dark' ? 'none' : 'inline';
            iconMoon.style.display = theme === 'dark' ? 'inline' : 'none';
        }
    }

    if (saved) setTheme(saved);

    toggle.addEventListener('click', function() {
        const current = document.documentElement.getAttribute('data-theme');
        setTheme(current === 'dark' ? 'light' : 'dark');
    });
})();

// --- Live search (client-side filtering on table rows) ---
(function() {
    const input = document.getElementById('liveSearch');
    const table = document.getElementById('aoTable');
    if (!input || !table) return;
    const tbody = table.querySelector('tbody');
    if (!tbody) return;

    input.addEventListener('input', function() {
        const q = this.value.toLowerCase().trim();
        const rows = tbody.querySelectorAll('tr');
        rows.forEach(function(row) {
            const text = row.textContent.toLowerCase();
            row.style.display = (!q || text.includes(q)) ? '' : 'none';
        });
    });
})();

// --- Browser notifications for urgent deadlines ---
(function() {
    if (!('Notification' in window)) return;

    function checkUrgents() {
        fetch('/api/urgents')
            .then(function(r) { return r.json(); })
            .then(function(urgents) {
                if (!urgents || !urgents.length) return;

                if (Notification.permission === 'default') {
                    Notification.requestPermission();
                }
                if (Notification.permission !== 'granted') return;

                var shown = JSON.parse(sessionStorage.getItem('ao-notifs-shown') || '[]');
                urgents.forEach(function(ao) {
                    if (shown.indexOf(ao.id) !== -1) return;
                    var jours = ao.jours_restants;
                    var msg = jours === 0 ? "Aujourd'hui !" :
                              jours === 1 ? "Demain !" :
                              "J-" + jours;
                    new Notification('AO Hunter - Deadline ' + msg, {
                        body: (ao.titre || '').substring(0, 80),
                        icon: '/static/css/style.css',
                        tag: 'ao-' + ao.id
                    });
                    shown.push(ao.id);
                });
                sessionStorage.setItem('ao-notifs-shown', JSON.stringify(shown));
            })
            .catch(function() {});
    }

    // Check on page load and every 5 minutes
    setTimeout(checkUrgents, 2000);
    setInterval(checkUrgents, 5 * 60 * 1000);
})();

// --- Toast notification helper ---
function showToast(msg, duration) {
    var toast = document.getElementById('notifToast');
    if (!toast) return;
    toast.textContent = msg;
    toast.style.display = 'block';
    setTimeout(function() {
        toast.style.display = 'none';
    }, duration || 4000);
}

// --- WebSocket auto-refresh ---
(function() {
    if (typeof io === 'undefined') return;
    var socket = io();

    socket.on('veille_complete', function(data) {
        if (data.nouveaux && data.nouveaux > 0) {
            showToast(data.nouveaux + ' nouveaux AO detectes ! Rechargement...');
            setTimeout(function() { location.reload(); }, 2000);
        }
    });
})();


// ===================================================================
// KEYBOARD SHORTCUTS
// ===================================================================

(function() {
    // Shortcuts overlay HTML
    var overlayHTML = '<div class="shortcuts-overlay" id="shortcutsOverlay">' +
        '<div class="shortcuts-panel">' +
        '<h2>Raccourcis clavier</h2>' +
        '<div class="shortcut-row"><span>Aller au Dashboard</span><span class="shortcut-key">Alt+D</span></div>' +
        '<div class="shortcut-row"><span>Liste des AO</span><span class="shortcut-key">Alt+A</span></div>' +
        '<div class="shortcut-row"><span>Kanban</span><span class="shortcut-key">Alt+K</span></div>' +
        '<div class="shortcut-row"><span>Recherche globale</span><span class="shortcut-key">Alt+S</span></div>' +
        '<div class="shortcut-row"><span>Resume du jour</span><span class="shortcut-key">Alt+R</span></div>' +
        '<div class="shortcut-row"><span>Concurrence</span><span class="shortcut-key">Alt+C</span></div>' +
        '<div class="shortcut-row"><span>CRM Acheteurs</span><span class="shortcut-key">Alt+M</span></div>' +
        '<div class="shortcut-row"><span>Dark mode</span><span class="shortcut-key">Alt+T</span></div>' +
        '<div class="shortcut-row"><span>Lancer veille</span><span class="shortcut-key">Alt+V</span></div>' +
        '<div class="shortcut-row"><span>Aide raccourcis</span><span class="shortcut-key">?</span></div>' +
        '<div style="text-align:center; margin-top:1rem; font-size:0.8rem; color:var(--text-light);">Appuyer sur Echap pour fermer</div>' +
        '</div></div>';

    document.body.insertAdjacentHTML('beforeend', overlayHTML);

    document.addEventListener('keydown', function(e) {
        var overlay = document.getElementById('shortcutsOverlay');

        // Ignorer si on est dans un input/textarea
        var tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') {
            if (e.key === 'Escape' && overlay) overlay.classList.remove('active');
            return;
        }

        // ? = aide
        if (e.key === '?' && !e.altKey && !e.ctrlKey) {
            e.preventDefault();
            if (overlay) overlay.classList.toggle('active');
            return;
        }

        // Escape = fermer overlay
        if (e.key === 'Escape') {
            if (overlay) overlay.classList.remove('active');
            return;
        }

        // Alt + touche
        if (e.altKey) {
            var routes = {
                'd': '/',
                'a': '/ao',
                'k': '/kanban',
                's': '/recherche',
                'r': '/resume',
                'c': '/concurrence',
                'm': '/crm',
            };

            var key = e.key.toLowerCase();

            if (routes[key]) {
                e.preventDefault();
                window.location.href = routes[key];
                return;
            }

            // Alt+T = dark mode toggle
            if (key === 't') {
                e.preventDefault();
                if (typeof toggleDarkMode === 'function') toggleDarkMode();
                return;
            }

            // Alt+V = lancer veille
            if (key === 'v') {
                e.preventDefault();
                fetch('/api/veille', {method: 'POST'}).then(function(r) { return r.json(); }).then(function(d) {
                    if (typeof showToast === 'function') showToast('Veille lancee !', 'info');
                });
                return;
            }
        }
    });
})();


// ===================================================================
// LIVE SEARCH DEBOUNCE (ameliore)
// ===================================================================

(function() {
    var searchInput = document.getElementById('liveSearch');
    if (!searchInput) return;

    var timer = null;
    searchInput.addEventListener('input', function() {
        clearTimeout(timer);
        var val = this.value.toLowerCase();
        timer = setTimeout(function() {
            var rows = document.querySelectorAll('#aoTable tbody tr');
            var count = 0;
            rows.forEach(function(row) {
                var match = row.textContent.toLowerCase().indexOf(val) >= 0;
                row.style.display = match ? '' : 'none';
                if (match) count++;
            });
            // Update count display if exists
            var countEl = document.getElementById('searchCount');
            if (countEl) countEl.textContent = count + ' resultat(s)';
        }, 200); // 200ms debounce
    });
})();
