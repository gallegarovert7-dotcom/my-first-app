from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
import whisper
import os
import re
import uuid
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "bisaya tongue"
CORS(app)


# --- CEBUANO PHONETIC RULES ENGINE ---
class CebuanoLinguisticEngine:

    # Phoneme rules: each word has IPA breakdown + common error patterns
    PHONETIC_MAP = {
        # Level 1: Colors
        "Pula":   {"ipa": "/ˈpu.la/",  "tips": ["Roll the 'U' short — like 'pool' without the 'l'", "Stress the first syllable: PU-la"], "common_errors": ["poola", "pola", "pula"]},
        "Asul":   {"ipa": "/ˈa.sul/",  "tips": ["'A' is wide open like 'ah'", "End with a short 'ul', not 'ool'"], "common_errors": ["azul", "asool", "asul"]},
        "Dalag":  {"ipa": "/ˈda.lag/", "tips": ["The final 'g' is a hard stop — don't soften it", "DA-lag, two crisp syllables"], "common_errors": ["dalag", "dalog", "dalak"]},
        "Berde":  {"ipa": "/ˈber.de/", "tips": ["'E' sounds like 'eh' not 'ee'", "The 'r' is slightly rolled"], "common_errors": ["verde", "birde", "berde"]},
        "Puti":   {"ipa": "/ˈpu.ti/",  "tips": ["'I' at the end is short — 'tee' very brief", "PU-ti with equal stress"], "common_errors": ["putee", "puti", "pooti"]},
        # Level 2: Animals
        "Iro":    {"ipa": "/ˈi.ɾo/",   "tips": ["Tapped 'r' — between 'r' and 'd' sound", "Short 'i' at the start, like 'ee' briefly"], "common_errors": ["ero", "iroh", "iro"]},
        "Iring":  {"ipa": "/ˈi.ɾiŋ/",  "tips": ["Nasal ending 'ng' like 'sing'", "The 'r' is a soft tap, not a hard American 'r'"], "common_errors": ["eering", "iring", "ireng"]},
        "Manok":  {"ipa": "/ˈma.nok/", "tips": ["Final 'k' is a glottal stop — cut it short", "MA-nok, stress on first syllable"], "common_errors": ["manoc", "manog", "manok"]},
        "Baka":   {"ipa": "/ˈba.ka/",  "tips": ["Both syllables short and crisp", "No stress on either — balanced BA-ka"], "common_errors": ["baka", "baca", "barka"]},
        "Baboy":  {"ipa": "/ˈba.boj/", "tips": ["'oy' diphthong — slides from 'o' to 'y'", "BA-boy, the 'boy' rhymes with English 'boy'"], "common_errors": ["baboi", "babuy", "baboy"]},
        # Level 3: Body Parts
        "Ulo":    {"ipa": "/ˈu.lo/",   "tips": ["'U' is pure — like 'oo' in 'food' briefly", "Soft 'l', not a hard American 'l'"], "common_errors": ["olo", "ulow", "ulo"]},
        "Kamot":  {"ipa": "/ˈka.mot/", "tips": ["Final 't' is a soft stop — barely voiced", "KA-mot, smooth transition between syllables"], "common_errors": ["kamut", "camot", "kamot"]},
        "Tuhod":  {"ipa": "/ˈtu.hod/", "tips": ["'H' is breathy, not guttural", "TU-hod — the 'd' at the end is cut short"], "common_errors": ["tuhud", "tohod", "tuhod"]},
        "Sapa":   {"ipa": "/ˈsa.pa/",  "tips": ["Pure 'a' vowels — like 'ah' twice", "SA-pa, evenly weighted syllables"], "common_errors": ["sappa", "sapha", "sapa"]},
        "Dunggan":{"ipa": "/ˈduŋ.gan/","tips": ["'ng' in the middle is nasal — like 'sung'", "DUNG-gan, stress on first syllable"], "common_errors": ["dungan", "doongan", "dunggan"]},
    }

    # General Cebuano phoneme rules
    RULES = [
        {"rule": "Vowel Purity",      "desc": "Cebuano has 3 pure vowels: A (/a/), I (/i/), U (/u/). Unlike English, they never glide — keep them short and clean."},
        {"rule": "Glottal Stops",     "desc": "Words ending in K, T, D often end with a glottal stop (sudden cutoff). Don't trail the vowel — clip it."},
        {"rule": "Tapped R (ɾ)",      "desc": "The Cebuano 'R' is a single tap — between English 'r' and 'd'. Like the 'tt' in 'butter' (American English)."},
        {"rule": "NG Cluster",        "desc": "'NG' is always a single nasal sound /ŋ/ — like the end of 'sing'. Never pronounce it as N+G separately."},
        {"rule": "Stress Patterns",   "desc": "Most 2-syllable Cebuano words stress the FIRST syllable. Listen for the natural weight — don't over-stress."},
        {"rule": "No Silent Letters", "desc": "Every letter is pronounced in Cebuano. There are no silent letters — what you see is what you say."},
    ]

    @staticmethod
    def normalize_bisaya(text):
        """Standardizes dialect variations for scoring."""
        text = text.lower().strip().replace('.', '').replace('?', '').replace(',', '')
        text = re.sub(r'[ou]', '(ou)', text)
        text = re.sub(r'[ei]', '(ei)', text)
        return text

    @staticmethod
    def get_phonetic_info(word):
        return CebuanoLinguisticEngine.PHONETIC_MAP.get(word, {
            "ipa": "",
            "tips": ["Speak clearly and at a moderate pace"],
            "common_errors": []
        })


# --- DATABASE LOGIC ---
def get_db():
    conn = sqlite3.connect('bisaya_system.db')
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'student'
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS user_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        word TEXT,
        level TEXT,
        accuracy REAL,
        timestamp DATETIME,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    # Pre/Post test results table
    conn.execute('''CREATE TABLE IF NOT EXISTS test_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        level TEXT,
        test_type TEXT,  -- 'pretest' or 'posttest'
        word TEXT,
        accuracy REAL,
        timestamp DATETIME,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    admin_exists = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
    if not admin_exists:
        conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                     ('admin', generate_password_hash("admin123"), 'admin'))
    conn.commit()
    conn.close()


init_db()

print("Loading Optimized Whisper Engine...")
model = whisper.load_model("tiny")


# --- AUTH ROUTES ---

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('index.html', user=session['username'], role=session.get('role', 'student'))


@app.route('/login')
def login_page():
    return render_template('login.html')


@app.route('/auth/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (data.get('username'),)).fetchone()
    conn.close()
    if user and check_password_hash(user['password'], data.get('password')):
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        return jsonify({"success": True})
    return jsonify({"error": "Invalid credentials"}), 401


@app.route('/auth/register', methods=['POST'])
def register():
    data = request.json
    try:
        conn = get_db()
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                     (data['username'], generate_password_hash(data['password'])))
        conn.commit()
        return jsonify({"success": True})
    except:
        return jsonify({"error": "Username already taken"}), 400
    finally:
        conn.close()


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# --- STATIC ASSET ROUTES (PWA) ---

@app.route('/static/manifest.json')
def serve_manifest():
    return app.send_static_file('manifest.json')

@app.route('/service-worker.js')
def serve_sw():
    return app.send_static_file('service-worker.js')


# --- PHONETIC RULES ROUTE ---

@app.route('/get_phonetic_rules')
def get_phonetic_rules():
    return jsonify(CebuanoLinguisticEngine.RULES)


@app.route('/get_phonetic_info/<word>')
def get_phonetic_info(word):
    return jsonify(CebuanoLinguisticEngine.get_phonetic_info(word))


# --- AI ANALYSIS ROUTE ---

@app.route('/analyze', methods=['POST'])
def analyze_audio():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401

    target_word = request.form.get('target', '')
    level_name = request.form.get('level', 'Foundation')
    test_type = request.form.get('test_type', None)  # 'pretest', 'posttest', or None (practice)
    audio_file = request.files['audio']
    filename = f"temp_{uuid.uuid4()}.wav"
    audio_file.save(filename)

    try:
        result = model.transcribe(
            filename,
            language="tl",
            initial_prompt="Pula, Asul, Iring, Manok, Bisaya, Cebuano",
            fp16=False
        )
        detected = result['text'].strip().lower().replace('.', '')

        norm_detected = CebuanoLinguisticEngine.normalize_bisaya(detected)
        norm_target = CebuanoLinguisticEngine.normalize_bisaya(target_word)

        score = SequenceMatcher(None, norm_detected, norm_target).ratio()
        accuracy = round(score * 100, 2)
        success = score >= 0.75

        conn = get_db()
        if test_type in ('pretest', 'posttest'):
            conn.execute(
                'INSERT INTO test_results (user_id, level, test_type, word, accuracy, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
                (session['user_id'], level_name, test_type, target_word, accuracy, datetime.now())
            )
        else:
            conn.execute(
                'INSERT INTO user_scores (user_id, word, level, accuracy, timestamp) VALUES (?, ?, ?, ?, ?)',
                (session['user_id'], target_word, level_name, accuracy, datetime.now())
            )
        conn.commit()
        conn.close()

        # Attach phonetic info in response
        phonetic = CebuanoLinguisticEngine.get_phonetic_info(target_word)
        return jsonify({
            "detected": detected,
            "accuracy": accuracy,
            "success": success,
            "phonetic": phonetic
        })
    finally:
        if os.path.exists(filename): os.remove(filename)


# --- MONITORING DATA ROUTES ---

@app.route('/get_admin_summary')
def get_admin_summary():
    if session.get('role') != 'admin': return jsonify([])
    conn = get_db()
    query = '''
        SELECT u.username, u.id,
               AVG(s.accuracy) as avg_accuracy, 
               MAX(s.level) as current_level,
               COUNT(s.id) as attempts
        FROM users u
        LEFT JOIN user_scores s ON u.id = s.user_id
        WHERE u.role = 'student'
        GROUP BY u.id
    '''
    rows = conn.execute(query).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/get_scores')
def get_scores():
    if 'user_id' not in session: return jsonify([])
    conn = get_db()
    if session['role'] == 'admin':
        rows = conn.execute('''
            SELECT u.username, s.word, s.level, s.accuracy, s.timestamp 
            FROM user_scores s 
            JOIN users u ON s.user_id = u.id 
            ORDER BY s.timestamp DESC
        ''').fetchall()
    else:
        rows = conn.execute('''
            SELECT word, level, accuracy, timestamp 
            FROM user_scores 
            WHERE user_id = ? 
            ORDER BY timestamp DESC
        ''', (session['user_id'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == '__main__':
    app.run(debug=True, port=5000)