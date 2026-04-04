"""Module d'authentification pour AO Hunter Dashboard."""
import os
import hashlib
from functools import wraps
from flask import redirect, url_for, request, session, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = 'Veuillez vous connecter pour acceder au dashboard.'

# Users from env vars or defaults
USERS = {
    'mickael': {
        'password_hash': hashlib.sha256(os.environ.get('AO_HUNTER_PASS', 'almera2026').encode()).hexdigest(),
        'nom': 'Mickael Bertolla',
        'role': 'admin',
    },
    'alternant': {
        'password_hash': hashlib.sha256(os.environ.get('AO_HUNTER_ALT_PASS', 'alternant2026').encode()).hexdigest(),
        'nom': 'Alternant',
        'role': 'operateur',
    },
}

class User(UserMixin):
    def __init__(self, username, data):
        self.id = username
        self.nom = data['nom']
        self.role = data['role']

    @property
    def is_admin(self):
        return self.role == 'admin'

@login_manager.user_loader
def load_user(username):
    if username in USERS:
        return User(username, USERS[username])
    return None

def check_login(username, password):
    user_data = USERS.get(username)
    if not user_data:
        return None
    if hashlib.sha256(password.encode()).hexdigest() == user_data['password_hash']:
        return User(username, user_data)
    return None

def init_auth(app):
    login_manager.init_app(app)
