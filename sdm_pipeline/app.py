from flask import Flask, request, jsonify, render_template
import sqlite3
import json
from datetime import datetime
import os

app = Flask(__name__)
DB_FILE = 'sdm_database.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            run_date DATETIME,
            raw_text TEXT,
            turns_json TEXT,
            regions_json TEXT
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/save', methods=['POST'])
def save_run():
    data = request.json
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO runs (filename, run_date, raw_text, turns_json, regions_json)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        data['filename'], 
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data['raw_text'],
        json.dumps(data['turns']),
        json.dumps(data['regions'])
    ))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/history', methods=['GET'])
def get_history():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Fetch everything except the massive JSON blobs for the list view
    cursor.execute('SELECT id, filename, run_date FROM runs ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    
    history = [{"id": r[0], "filename": r[1], "run_date": r[2]} for r in rows]
    return jsonify(history)

@app.route('/api/run/<int:run_id>', methods=['GET'])
def get_run(run_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT filename, raw_text, turns_json, regions_json FROM runs WHERE id = ?', (run_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return jsonify({
            "filename": row[0],
            "raw_text": row[1],
            "turns": json.loads(row[2]),
            "regions": json.loads(row[3])
        })
    return jsonify({"error": "Not found"}), 404

if __name__ == '__main__':
    init_db()
    print("Starting SDM Pipeline Server on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)