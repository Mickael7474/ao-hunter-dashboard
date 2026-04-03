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
