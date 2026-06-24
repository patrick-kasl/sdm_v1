from flask import Flask, request, jsonify, render_template
import sqlite3
import json
from datetime import datetime

app = Flask(__name__)
DB_NAME = "consent_runs.db"

# --- FIX: Auto-create tables if the DB is deleted ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            run_date TEXT,
            raw_text TEXT,
            turns_json TEXT,
            regions_json TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Run this immediately when the app starts
init_db()

@app.route('/')
def index():
    # Assuming your HTML file is named index.html and is in a 'templates' folder
    return render_template('index.html') 

@app.route('/api/history', methods=['GET'])
def get_history():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, filename, run_date FROM runs ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"id": r[0], "filename": r[1], "run_date": r[2]} for r in rows])

@app.route('/api/run/<int:run_id>', methods=['GET'])
def get_run(run_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT turns_json, regions_json FROM runs WHERE id = ?', (run_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return jsonify({
            "turns": json.loads(row[0]),
            "regions": json.loads(row[1])
        })
    return jsonify({"error": "Not found"}), 404

@app.route('/api/save', methods=['POST'])
def save_run():
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO runs (filename, run_date, raw_text, turns_json, regions_json)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        data.get('filename', 'Unknown'),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data.get('raw_text', ''),
        json.dumps(data.get('turns', [])),
        json.dumps(data.get('regions', []))
    ))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# --- NEW: Endpoint to safely clear the history ---
@app.route('/api/clear', methods=['POST'])
def clear_history():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM runs')
    conn.commit()
    conn.close()
    return jsonify({"status": "cleared"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)