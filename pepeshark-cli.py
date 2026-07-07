import sqlite3
import secrets
import string
import sys
import getpass
from werkzeug.security import generate_password_hash

DB_PATH = "auth.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT)")

def generate_secure_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for i in range(length))

def add_admin():
    username = input("Enter new admin username (or leave blank to auto-generate): ").strip()
    if not username:
        username = "admin_" + ''.join(secrets.choice(string.digits) for _ in range(4))
    
    gen_pass = input("Auto-generate secure password? (y/n): ").strip().lower()
    if gen_pass == 'y':
        password = generate_secure_password()
        print(f"\n--- GENERATED CREDENTIALS ---")
        print(f"Username: {username}")
        print(f"Password: {password}")
        print(f"-----------------------------\n")
    else:
        password = getpass.getpass("Enter password: ")
        
    hashed = generate_password_hash(password)
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed))
            print(f"Admin '{username}' added successfully.")
    except sqlite3.IntegrityError:
        print("Error: Username already exists.")

def view_logs():
    print("\n--- System Logs ---")
    print("Log viewer initialized. The gateway routes all standard output to the console.")
    print("To view real-time HTTP traffic, check the terminal running 'start_all.py'.")
    print("-------------------\n")

def restart_service():
    print("\n[!] To safely restart the unified service:")
    print("1. Press Ctrl+C in the window running 'start_all.py' to terminate all microservices.")
    print("2. Run 'python start_all.py' again to boot them back up.\n")

def main():
    init_db()
    while True:
        print("\n=== Gateway Admin CLI ===")
        print("1. Add new admin user")
        print("2. View system instructions / logs")
        print("3. Restart unified service")
        print("4. Exit")
        choice = input("Select an option (1-4): ")
        
        if choice == '1':
            add_admin()
        elif choice == '2':
            view_logs()
        elif choice == '3':
            restart_service()
        elif choice == '4':
            print("Exiting CLI.")
            sys.exit(0)
        else:
            print("Invalid option.")

if __name__ == "__main__":
    main()
