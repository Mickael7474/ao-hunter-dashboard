"""Wrapper to ensure user site-packages are available."""
import sys
import os
import site

# Force user site-packages
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# Set __file__ context for app.py
os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, socketio

if __name__ == "__main__":
    print("AO Hunter Dashboard - http://localhost:5000")
    socketio.run(app, debug=True, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
