import sqlite3
import requests
from flask import Flask, request, session, redirect, url_for, render_template_string, Response
from werkzeug.security import check_password_hash

# Disable default static folder so we can proxy /static/
app = Flask(__name__, static_folder=None)
app.secret_key = "super_secret_gateway_key_change_in_production"
DB_PATH = "auth.db"

# Ensure the DB exists
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT)")
init_db()

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Gateway Login</title></head>
<body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f0f2f5;">
    <div style="background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 300px;">
        <h2 style="text-align: center;">Unified Gateway</h2>
        {% if error %}<p style="color: red; text-align: center;">{{ error }}</p>{% endif %}
        <form method="POST">
            <div style="margin-bottom: 1rem;">
                <label>Username</label><br>
                <input type="text" name="username" required style="width: 100%; padding: 0.5rem; box-sizing: border-box;">
            </div>
            <div style="margin-bottom: 1rem;">
                <label>Password</label><br>
                <input type="password" name="password" required style="width: 100%; padding: 0.5rem; box-sizing: border-box;">
            </div>
            <button type="submit" style="width: 100%; padding: 0.5rem; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">Login</button>
        </form>
    </div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head><title>Unified Dashboard</title>
<style>
    body { font-family: sans-serif; margin: 0; padding: 0; background: #f0f2f5; }
    .navbar { background: #1f2937; color: white; padding: 1rem; display: flex; justify-content: space-between; align-items: center; }
    .nav-btn { color: white; text-decoration: none; padding: 0.5rem 1rem; background: #374151; border-radius: 4px; margin-left: 0.5rem; }
    .nav-btn:hover { background: #4b5563; }
    .container { padding: 2rem; display: flex; gap: 2rem; justify-content: center; margin-top: 2rem;}
    .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; width: 300px; border-top: 4px solid #007bff; }
    .card a { display: inline-block; margin-top: 1rem; padding: 0.75rem 1.5rem; background: #007bff; color: white; text-decoration: none; border-radius: 4px; font-weight: bold; }
</style>
</head>
<body>
    <div class="navbar">
        <h2>Unified Dashboard</h2>
        <div>
            <span style="margin-right: 1rem;">Admin: {{ session['username'] }}</span>
            <a href="/logout" class="nav-btn">Logout</a>
        </div>
    </div>
    <div class="container">
        <div class="card">
            <h3>Pasarguard Tor</h3>
            <p>Access the Tor network control panel running on Port 54322.</p>
            <a href="/tor/">Open Tor Panel</a>
        </div>
        <div class="card">
            <h3>Pepeshark</h3>
            <p>Access the Surfshark network control panel running on Port 8088.</p>
            <a href="/surfshark/">Open Surfshark Panel</a>
        </div>
    </div>
</body>
</html>
"""

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            if row and check_password_hash(row[0], password):
                session["logged_in"] = True
                session["username"] = username
                return redirect(url_for("dashboard"))
            else:
                error = "Invalid credentials"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.before_request
def require_login():
    if not session.get("logged_in") and request.endpoint != "login":
        return redirect(url_for("login"))

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

# Reverse proxy for Tor Panel (Port 54322)
@app.route("/tor/", defaults={"path": ""})
@app.route("/tor/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def proxy_tor(path):
    return _proxy_request("http://127.0.0.1:54322", request.path.replace("/tor", "", 1) or "/", request)

# Reverse proxy for Surfshark Panel (Port 8088)
@app.route("/surfshark/", defaults={"path": ""})
@app.route("/surfshark/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def proxy_surfshark(path):
    return _proxy_request("http://127.0.0.1:8088", request.path.replace("/surfshark", "", 1) or "/", request)

# Catch-all proxy for /static/ and /api/ based on Referer header
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def proxy_shared(path):
    referer = request.headers.get("Referer", "")
    if "/tor/" in referer:
        base_url = "http://127.0.0.1:54322"
    elif "/surfshark/" in referer:
        base_url = "http://127.0.0.1:8088"
    else:
        # If no referer is available, try to route based on path hints or default to Tor
        if "surfshark" in path:
            base_url = "http://127.0.0.1:8088"
        else:
            base_url = "http://127.0.0.1:54322"

    return _proxy_request(base_url, "/" + path, request)

def _proxy_request(base_url, path, req):
    url = f"{base_url}{path}"
    if req.query_string:
        url += f"?{req.query_string.decode('utf-8')}"
    
    try:
        # Forward the request to the underlying microservice
        resp = requests.request(
            method=req.method,
            url=url,
            headers={key: value for (key, value) in req.headers if key.lower() != 'host'},
            data=req.get_data(),
            cookies=req.cookies,
            allow_redirects=False,
            timeout=10)
        
        # Exclude headers that cause issues with Flask's response mechanism
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in resp.raw.headers.items()
                   if name.lower() not in excluded_headers]
        return Response(resp.content, resp.status_code, headers)
    except requests.exceptions.RequestException as e:
        error_msg = f"<h3>Service Unavailable (502 Bad Gateway)</h3><p>The microservice at <b>{base_url}</b> is currently down or starting up.</p><p>Please check the service status on the server.</p>"
        return error_msg, 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
