import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import sqlite3
import json
import smtplib
import schedule
import threading
import time
import pandas as pd
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import hashlib
import base64
from cryptography.fernet import Fernet
import webbrowser

# Google Calendar API imports
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False
    print("Google API kliens nincs telepítve. pip install google-api-python-client google-auth-oauthlib telepítés szükséges")

class DatabaseManager:
    """Adatbázis kezelő osztály"""    
    def __init__(self, db_name="patient_reminder.db"):
        self.db_name = db_name
        self.init_database()
    
    def init_database(self):
        """Adatbázis inicializálása"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Páciensek tábla
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                language TEXT DEFAULT 'hu',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active INTEGER DEFAULT 1
            )
        ''')
        
        # Email sablonok tábla
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                template_type TEXT DEFAULT 'reminder'
            )
        ''')
        
        # Napló tábla
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                patient_email TEXT
            )
        ''')
        
        # Naptár események tábla
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                google_event_id TEXT UNIQUE,
                patient_email TEXT,
                event_title TEXT,
                event_description TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reminder_sent INTEGER DEFAULT 0,
                is_new_appointment INTEGER DEFAULT 0,
                new_appointment_notified INTEGER DEFAULT 0
            )
        ''')
        
        conn.commit()
        conn.close()
        
        # Adatbázis migráció végrehajtása
        self.migrate_database()
        
        # Alapértelmezett sablonok beszúrása
        self.insert_default_templates()
    
    def migrate_database(self):
        """Adatbázis migráció - új oszlopok hozzáadása"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Ellenőrizzük, hogy léteznek-e az új oszlopok
            cursor.execute("PRAGMA table_info(calendar_events)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # is_new_appointment oszlop hozzáadása ha hiányzik
            if 'is_new_appointment' not in columns:
                cursor.execute('ALTER TABLE calendar_events ADD COLUMN is_new_appointment INTEGER DEFAULT 0')
                print("Adatbázis migráció: is_new_appointment oszlop hozzáadva")
            
            # new_appointment_notified oszlop hozzáadása ha hiányzik
            if 'new_appointment_notified' not in columns:
                cursor.execute('ALTER TABLE calendar_events ADD COLUMN new_appointment_notified INTEGER DEFAULT 0')
                print("Adatbázis migráció: new_appointment_notified oszlop hozzáadva")
            
            conn.commit()
            conn.close()
            
            return True
            
        except Exception as e:
            print(f"Adatbázis migrációs hiba: {str(e)}")
            return False
        
    def insert_default_templates(self):
        """Alapértelmezett email sablonok beszúrása"""
        templates = [
            {
                'name': 'Magyar emlékeztető',
                'language': 'hu',
                'subject': 'Emlékeztető - Időpontja holnap',
                'body': '''Kedves {patient_name}!

Emlékeztetjük, hogy holnap ({appointment_date}) {appointment_time}-kor időpontja van nálunk.

Kérjük, érkezzen pontosan!

Üdvözlettel,
{clinic_name}''',
                'template_type': 'reminder'
            },
            {
                'name': 'Német emlékeztető',
                'language': 'de',
                'subject': 'Erinnerung - Ihr Termin morgen',
                'body': '''Liebe/r {patient_name}!

Wir möchten Sie daran erinnern, dass Sie morgen ({appointment_date}) um {appointment_time} einen Termin bei uns haben.

Bitte kommen Sie pünktlich!

Mit freundlichen Grüßen,
{clinic_name}''',
                'template_type': 'reminder'
            }
        ]
        
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        for template in templates:
            cursor.execute('''
                INSERT OR IGNORE INTO email_templates 
                (name, language, subject, body, template_type) 
                VALUES (?, ?, ?, ?, ?)
            ''', (template['name'], template['language'], template['subject'], 
                 template['body'], template['template_type']))
        
        conn.commit()
        conn.close()
    
    def add_patient(self, name, email, phone="", language="hu"):
        """Páciens hozzáadása"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO patients (name, email, phone, language) 
                VALUES (?, ?, ?, ?)
            ''', (name, email, phone, language))
            conn.commit()
            patient_id = cursor.lastrowid
            conn.close()
            return patient_id
        except Exception as e:
            raise ValueError(f"Páciens hozzáadási hiba: {str(e)}")
    
    def get_patients(self, active_only=True):
        """Páciensek lekérése"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        if active_only:
            cursor.execute('SELECT * FROM patients WHERE active = 1 ORDER BY name')
        else:
            cursor.execute('SELECT * FROM patients ORDER BY name')
        
        patients = cursor.fetchall()
        conn.close()
        return patients
    
    def get_patient_by_email(self, email):
        """Páciens lekérése email alapján"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM patients WHERE email = ? AND active = 1', (email,))
        patient = cursor.fetchone()
        conn.close()
        return patient
    
    def delete_patient(self, patient_id):
        """Páciens fizikai törlése az adatbázisból"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM patients WHERE id = ?', (patient_id,))
            conn.commit()
            deleted_count = cursor.rowcount
            conn.close()
            return deleted_count > 0
        except Exception as e:
            print(f"Páciens törlési hiba: {str(e)}")
            return False
    
    def add_calendar_event(self, google_event_id, patient_email, event_title, event_description, start_time, end_time, is_new=False):
        """Naptár esemény hozzáadása"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO calendar_events 
                (google_event_id, patient_email, event_title, event_description, start_time, end_time, is_new_appointment) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (google_event_id, patient_email, event_title, event_description, start_time, end_time, 1 if is_new else 0))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Naptár esemény hozzáadási hiba: {str(e)}")
            return False
    
    def get_calendar_events(self, days_ahead=30):
        """Naptár események lekérése"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        end_date = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d %H:%M:%S')
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
            SELECT * FROM calendar_events 
            WHERE start_time BETWEEN ? AND ? 
            ORDER BY start_time
        ''', (current_date, end_date))
        
        events = cursor.fetchall()
        conn.close()
        return events
    
    def get_tomorrows_reminders(self):
        """Holnapi emlékeztetők lekérése"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        tomorrow = datetime.now() + timedelta(days=1)
        tomorrow_start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
        tomorrow_end = tomorrow.replace(hour=23, minute=59, second=59, microsecond=999999).strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
            SELECT * FROM calendar_events 
            WHERE start_time BETWEEN ? AND ? 
            AND reminder_sent = 0
            ORDER BY start_time
        ''', (tomorrow_start, tomorrow_end))
        
        events = cursor.fetchall()
        conn.close()
        return events
    
    def get_todays_new_appointments(self):
        """Mai új időpontok lekérése"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        today = datetime.now()
        today_start = today.replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
        today_end = today.replace(hour=23, minute=59, second=59, microsecond=999999).strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
            SELECT * FROM calendar_events 
            WHERE created_at BETWEEN ? AND ? 
            AND is_new_appointment = 1 
            AND new_appointment_notified = 0
            ORDER BY start_time
        ''', (today_start, today_end))
        
        events = cursor.fetchall()
        conn.close()
        return events
    
    def mark_reminder_sent(self, event_id):
        """Emlékeztető küldés megjelölése"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('UPDATE calendar_events SET reminder_sent = 1 WHERE id = ?', (event_id,))
        conn.commit()
        conn.close()
    
    def mark_new_appointment_notified(self, event_id):
        """Új időpont értesítés megjelölése"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('UPDATE calendar_events SET new_appointment_notified = 1 WHERE id = ?', (event_id,))
        conn.commit()
        conn.close()
    
    def delete_calendar_event(self, event_id):
        """Naptár esemény törlése"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM calendar_events WHERE id = ?', (event_id,))
            conn.commit()
            deleted_count = cursor.rowcount
            conn.close()
            return deleted_count > 0
        except Exception as e:
            print(f"Naptár esemény törlési hiba: {str(e)}")
            return False
    
    def add_log(self, level, message, patient_email=None):
        """Napló bejegyzés hozzáadása"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO logs (level, message, patient_email) 
            VALUES (?, ?, ?)
        ''', (level, message, patient_email))
        conn.commit()
        conn.close()
    
    def get_logs(self, limit=100):
        """Napló bejegyzések lekérése"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp, level, message, patient_email 
            FROM logs 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (limit,))
        logs = cursor.fetchall()
        conn.close()
        return logs

class SecurityManager:
    """Biztonsági kezelő osztály"""
    def __init__(self):
        self.key_file = "encryption.key"
        self.key = self.load_or_create_key()
        self.cipher_suite = Fernet(self.key)
    
    def load_or_create_key(self):
        """Titkosítási kulcs betöltése vagy létrehozása"""
        if os.path.exists(self.key_file):
            with open(self.key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            return key
    
    def encrypt_password(self, password):
        """Jelszó titkosítása"""
        return self.cipher_suite.encrypt(password.encode()).decode()
    
    def decrypt_password(self, encrypted_password):
        """Jelszó visszafejtése"""
        return self.cipher_suite.decrypt(encrypted_password.encode()).decode()

class GoogleCalendarManager:
    """Google Calendar kezelő osztály"""
    def __init__(self):
        self.SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
        self.credentials = None
        self.service = None
        
        # Dinamikus elérési utak
        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.credentials_path = os.path.join(self.base_path, 'credentials.json')
        self.token_path = os.path.join(self.base_path, 'token.json')
        
    def authenticate(self):
        """Google Calendar authentikáció"""
        if not GOOGLE_API_AVAILABLE:
            raise ImportError("Google API kliens nincs telepítve")
        
        creds = None
        
        # Token fájl ellenőrzése
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)
        
        # Ha nincs érvényes credential, authentikáció
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                # credentials.json fájl ellenőrzése
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(f"credentials.json fájl hiányzik itt: {self.credentials_path}")
                
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, self.SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Token mentése
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())
        
        self.credentials = creds
        self.service = build('calendar', 'v3', credentials=creds)
        return True
    
    def get_upcoming_events(self, days_ahead=30):
        """Következő események lekérése"""
        if not self.service:
            self.authenticate()
        
        # Időintervallum beállítása
        now = datetime.utcnow().isoformat() + 'Z'
        end_time = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat() + 'Z'
        
        # Események lekérése
        events_result = self.service.events().list(
            calendarId='primary',
            timeMin=now,
            timeMax=end_time,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        return events
    
    def parse_event_for_patient(self, event):
        """Esemény elemzése páciens adatok kinyerésére"""
        # Egyszerű email keresés a leírásban vagy címben
        description = event.get('description', '')
        summary = event.get('summary', '')
        
        # Email minta keresése
        import re
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        
        emails = re.findall(email_pattern, description + ' ' + summary)
        
        if emails:
            return emails[0]  # Első email visszaadása
        
        return None

class EmailManager:
    """Email kezelő osztály"""
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.security_manager = SecurityManager()
    
    def send_email(self, to_email, subject, body, patient_name=""):
        """Email küldése"""
        try:
            config = self.config_manager.get_email_config()
            
            # SMTP szerver kapcsolat
            server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
            server.starttls()
            
            # Jelszó visszafejtése
            password = self.security_manager.decrypt_password(config['password'])
            server.login(config['email'], password)
            
            # Email összeállítása
            msg = MIMEMultipart()
            msg['From'] = config['email']
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # Body személyre szabása
            personalized_body = body.replace('{patient_name}', patient_name)
            personalized_body = personalized_body.replace('{clinic_name}', config.get('clinic_name', 'Rendelő'))
            
            msg.attach(MIMEText(personalized_body, 'plain', 'utf-8'))
            
            # Email küldése
            server.send_message(msg)
            server.quit()
            
            return True, "Email sikeresen elküldve"
            
        except Exception as e:
            return False, f"Email küldési hiba: {str(e)}"
    
    def send_appointment_reminder(self, patient_email, patient_name, appointment_date, appointment_time):
        """Időpont emlékeztető küldése"""
        try:
            # Sablon lekérése
            subject = "Emlékeztető - Időpontja holnap"
            body = f"""Kedves {patient_name}!

Emlékeztetjük, hogy holnap ({appointment_date}) {appointment_time}-kor időpontja van nálunk.

Kérjük, érkezzen pontosan!

Üdvözlettel,
{self.config_manager.get_email_config().get('clinic_name', 'Rendelő')}"""
            
            return self.send_email(patient_email, subject, body, patient_name)
            
        except Exception as e:
            return False, f"Emlékeztető küldési hiba: {str(e)}"
    
    def send_new_appointment_notification(self, patient_email, patient_name, appointment_date, appointment_time):
        """Új időpont értesítés küldése"""
        try:
            subject = "Új időpont visszaigazolás"
            body = f"""Kedves {patient_name}!

Megerősítjük az új időpontot:

Dátum: {appointment_date}
Időpont: {appointment_time}

Ha bármilyen kérdése van, keressen minket!

Üdvözlettel,
{self.config_manager.get_email_config().get('clinic_name', 'Rendelő')}"""
            
            return self.send_email(patient_email, subject, body, patient_name)
            
        except Exception as e:
            return False, f"Új időpont értesítési hiba: {str(e)}"
    
    def send_test_email(self):
        """Teszt email küldése"""
        try:
            config = self.config_manager.get_email_config()
            
            if not config['email'] or not config['password']:
                return False, "Email beállítások hiányosak"
            
            return self.send_email(
                config['email'],
                "Teszt email - Páciens emlékeztető rendszer",
                "Ez egy teszt email. Ha megkapta, a beállítások helyesek!\n\nÜdvözlettel,\nPáciens emlékeztető rendszer",
                "Teszt Felhasználó"
            )
            
        except Exception as e:
            return False, f"Teszt email küldési hiba: {str(e)}"

class ConfigManager:
    """Konfigurációs kezelő osztály"""
    def __init__(self):
        self.config_file = "config.json"
        self.security_manager = SecurityManager()
        self.load_config()
    
    def load_config(self):
        """Konfiguráció betöltése"""
        default_config = {
            'email': {
                'smtp_server': 'smtp.gmail.com',
                'smtp_port': 587,
                'email': '',
                'password': '',
                'clinic_name': 'Orvosi Rendelő'
            },
            'automation': {
                'reminder_time': '12:00',  # Emlékeztetők küldése
                'new_appointment_time': '15:30',  # Új időpontok értesítése
                'enabled': False
            },
            'google_calendar': {
                'enabled': False,
                'calendar_id': 'primary'
            }
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # Meglévő konfigurációval frissítés
                    self.merge_config(default_config, loaded_config)
                    self.config = default_config
            except:
                self.config = default_config
        else:
            self.config = default_config
    
    def merge_config(self, default, loaded):
        """Konfigurációk egyesítése"""
        for key, value in loaded.items():
            if key in default:
                if isinstance(value, dict) and isinstance(default[key], dict):
                    self.merge_config(default[key], value)
                else:
                    default[key] = value
    
    def save_config(self):
        """Konfiguráció mentése"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
    
    def get_email_config(self):
        """Email konfiguráció lekérése"""
        return self.config['email']
    
    def set_email_config(self, smtp_server, smtp_port, email, password, clinic_name):
        """Email konfiguráció beállítása"""
        # Jelszó titkosítása
        encrypted_password = self.security_manager.encrypt_password(password)
        
        self.config['email'] = {
            'smtp_server': smtp_server,
            'smtp_port': smtp_port,
            'email': email,
            'password': encrypted_password,
            'clinic_name': clinic_name
        }
        self.save_config()

class AutomationManager:
    """Automatizálási kezelő osztály"""
    def __init__(self, db_manager, config_manager, email_manager, calendar_manager):
        self.db_manager = db_manager
        self.config_manager = config_manager
        self.email_manager = email_manager
        self.calendar_manager = calendar_manager
        self.running = False
        self.thread = None
    
    def start_automation(self):
        """Automatizálás indítása"""
        if not self.running:
            self.running = True
            self.setup_schedule()
            self.thread = threading.Thread(target=self.run_scheduler, daemon=True)
            self.thread.start()
            self.db_manager.add_log("INFO", "Automatizálás elindítva")
    
    def stop_automation(self):
        """Automatizálás leállítása"""
        self.running = False
        schedule.clear()
        self.db_manager.add_log("INFO", "Automatizálás leállítva")
    
    def setup_schedule(self):
        """Ütemezett feladatok beállítása"""
        config = self.config_manager.config['automation']
        
        # Emlékeztetők küldése naponta 12:00-kor
        schedule.every().day.at(config['reminder_time']).do(self.send_daily_reminders)
        
        # Új időpontok értesítése naponta 15:30-kor
        schedule.every().day.at(config['new_appointment_time']).do(self.send_new_appointment_notifications)
    
    def run_scheduler(self):
        """Ütemező futtatása"""
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # 1 perc várakozás
    
    def send_daily_reminders(self):
        """Napi emlékeztetők küldése (holnapi időpontokra)"""
        try:
            if not self.config_manager.config['automation']['enabled']:
                return
            
            reminders = self.db_manager.get_tomorrows_reminders()
            sent_count = 0
            
            for event in reminders:
                try:
                    patient_email = event[2]
                    if patient_email:
                        patient = self.db_manager.get_patient_by_email(patient_email)
                        
                        if patient:
                            start_time = datetime.strptime(event[5], '%Y-%m-%d %H:%M:%S')
                            appointment_date = start_time.strftime("%Y-%m-%d")
                            appointment_time = start_time.strftime("%H:%M")
                            
                            success, message = self.email_manager.send_appointment_reminder(
                                patient_email, patient[1], appointment_date, appointment_time
                            )
                            
                            if success:
                                self.db_manager.mark_reminder_sent(event[0])
                                self.db_manager.add_log("INFO", f"Napi emlékeztető elküldve: {patient[1]}", patient_email)
                                sent_count += 1
                            else:
                                self.db_manager.add_log("ERROR", f"Emlékeztető hiba: {message}", patient_email)
                
                except Exception as e:
                    self.db_manager.add_log("ERROR", f"Emlékeztető feldolgozási hiba: {str(e)}")
            
            if sent_count > 0:
                self.db_manager.add_log("INFO", f"Napi emlékeztető kör befejezve: {sent_count} email elküldve")
        
        except Exception as e:
            self.db_manager.add_log("ERROR", f"Napi emlékeztető hiba: {str(e)}")
    
    def send_new_appointment_notifications(self):
        """Mai új időpontok értesítése"""
        try:
            if not self.config_manager.config['automation']['enabled']:
                return
            
            new_appointments = self.db_manager.get_todays_new_appointments()
            sent_count = 0
            
            for event in new_appointments:
                try:
                    patient_email = event[2]
                    if patient_email:
                        patient = self.db_manager.get_patient_by_email(patient_email)
                        
                        if patient:
                            start_time = datetime.strptime(event[5], '%Y-%m-%d %H:%M:%S')
                            appointment_date = start_time.strftime("%Y-%m-%d")
                            appointment_time = start_time.strftime("%H:%M")
                            
                            success, message = self.email_manager.send_new_appointment_notification(
                                patient_email, patient[1], appointment_date, appointment_time
                            )
                            
                            if success:
                                self.db_manager.mark_new_appointment_notified(event[0])
                                self.db_manager.add_log("INFO", f"Új időpont értesítés elküldve: {patient[1]}", patient_email)
                                sent_count += 1
                            else:
                                self.db_manager.add_log("ERROR", f"Új időpont értesítési hiba: {message}", patient_email)
                
                except Exception as e:
                    self.db_manager.add_log("ERROR", f"Új időpont értesítési feldolgozási hiba: {str(e)}")
            
            if sent_count > 0:
                self.db_manager.add_log("INFO", f"Új időpont értesítési kör befejezve: {sent_count} email elküldve")
        
        except Exception as e:
            self.db_manager.add_log("ERROR", f"Új időpont értesítési hiba: {str(e)}")

class ModernPatientReminderApp:
    """Modern Patient Reminder alkalmazás"""
    def __init__(self, root):
        self.root = root
        self.root.title("Páciens email emlékeztető rendszer v2.0")
        self.root.geometry("1600x1000")
        
        # Modern színpaletta
        self.colors = {
            'border': '#DEE2E6',
            'primary': "#0F7CAF",      # acélkék
            'secondary': '#20B2AA',    # Light Sea Green  
            'accent': '#32CD32',       # Lime Green
            'bg_main': '#F8F9FA',      # Light Gray
            'bg_card': '#FFFFFF',      # White
            'text_dark': '#2C3E50',    # Dark Blue Gray
            'text_light': '#6C757D',   # Gray
            'success': '#28A745',      # Green
            'warning': '#FFC107',      # Yellow
            'danger': '#DC3545'        # Red
        }
        
        self.root.configure(bg=self.colors['bg_main'])
        
        # Komponensek inicializálása
        self.db_manager = DatabaseManager()
        self.config_manager = ConfigManager()
        self.security_manager = SecurityManager()
        self.email_manager = EmailManager(self.config_manager)
        
        try:
            self.calendar_manager = GoogleCalendarManager()
        except:
            self.calendar_manager = None
        
        self.automation_manager = AutomationManager(
            self.db_manager, self.config_manager, 
            self.email_manager, self.calendar_manager
        )
        
        # GUI változók inicializálása
        self.init_variables()
        
        # Modern stílus beállítása
        self.setup_styles()
        
        # GUI létrehozása
        self.create_gui()
        
        # Bezárás kezelése
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def init_variables(self):
        """GUI változók inicializálása"""
        # Email beállítások
        self.email_username = tk.StringVar()
        self.email_password = tk.StringVar()
        self.smtp_server = tk.StringVar(value='smtp.gmail.com')
        self.smtp_port = tk.StringVar(value='587')
        self.clinic_name = tk.StringVar(value='Orvosi Rendelő')
        
        # Új páciens
        self.new_patient_name = tk.StringVar()
        self.new_patient_email = tk.StringVar()
        self.new_patient_phone = tk.StringVar()
        self.new_patient_language = tk.StringVar(value='hu')
        
        # Keresés 
        self.search_term = tk.StringVar()
        self.all_patients = []  # Összes páciens tárolása a szűréshez
        
        # Státusz
        self.automation_status = tk.StringVar(value="Leállítva")
        self.calendar_status = tk.StringVar(value="Nincs kapcsolat")
        
        # Azonnali üzenet
        self.message_subject = tk.StringVar()
        self.message_recipients = tk.StringVar(value='all')
    
    def setup_styles(self):
        """Modern stílusok beállítása"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # Notebook stílusok
        style.configure('Modern.TNotebook',
                       background=self.colors['bg_main'],
                       borderwidth=0,
                       tabposition='n')
        
        style.configure('Modern.TNotebook.Tab',
                    padding=[15, 12],
                    font=('Segoe UI', 10, 'normal'),
                    focuscolor='none',
                    expand=[1, 1, 1, 1])

        # Tab állapotok színezése
        style.map('Modern.TNotebook.Tab',
                 background=[('selected', self.colors['primary']),
                            ('active', self.colors['secondary']),
                            ('!active', '#E9ECEF')],
                 foreground=[('selected', 'white'),
                            ('active', 'white'),
                            ('!active', self.colors['text_dark'])],
                 borderwidth=[('selected', 3),
                             ('!selected', 1)])
        
        # Frame stílusok
        style.configure('Main.TFrame', background=self.colors['bg_main'])
        style.configure('Card.TFrame',
                       background=self.colors['bg_card'],
                       relief='flat',
                       borderwidth=1)
        
        # LabelFrame stílusok
        style.configure('Modern.TLabelframe',
                       background=self.colors['bg_card'],
                       relief='flat',
                       borderwidth=2,
                       labelanchor='n')
        style.configure('Modern.TLabelframe.Label',
                       background=self.colors['bg_card'],
                       foreground=self.colors['text_dark'],
                       font=('Segoe UI', 11, 'bold'))
        
        # Label stílusok
        style.configure('Modern.TLabel', 
                       background=self.colors['bg_card'], 
                       foreground=self.colors['text_dark'],
                       font=('Segoe UI', 10))
        style.configure('Title.TLabel', 
                       background=self.colors['bg_main'], 
                       foreground=self.colors['text_dark'],
                       font=('Segoe UI', 16, 'bold'))
        style.configure('Status.TLabel', 
                       background=self.colors['bg_card'], 
                       foreground=self.colors['primary'],
                       font=('Segoe UI', 12, 'bold'))
        
        # Entry stílusok
        style.configure('Modern.TEntry', 
                       fieldbackground=self.colors['bg_card'], 
                       borderwidth=2,
                       relief='solid', 
                       padding=8,
                       font=('Segoe UI', 10))
        style.configure('Modern.TCombobox', 
                       fieldbackground=self.colors['bg_card'], 
                       borderwidth=2,
                       relief='solid', 
                       padding=8,
                       font=('Segoe UI', 10))
        
        # Button stílusok
        style.configure('Primary.TButton',
                       background=self.colors['primary'],
                       foreground='white',
                       font=('Segoe UI', 10, 'bold'),
                       borderwidth=0,
                       focuscolor='none',
                       padding=[20, 12])
        style.map('Primary.TButton',
                 background=[('active', self.colors['secondary']),
                            ('pressed', self.colors['accent'])])
        
        style.configure('Secondary.TButton',
                       background=self.colors['secondary'],
                       foreground='white',
                       font=('Segoe UI', 10),
                       borderwidth=0,
                       focuscolor='none',
                       padding=[15, 10])
        
        style.configure('Success.TButton',
                       background=self.colors['success'],
                       foreground='white',
                       font=('Segoe UI', 10, 'bold'),
                       borderwidth=0,
                       focuscolor='none',
                       padding=[20, 12])
        
        style.configure('Danger.TButton',
                       background=self.colors['danger'],
                       foreground='white',
                       font=('Segoe UI', 10, 'bold'),
                       borderwidth=0,
                       focuscolor='none',
                       padding=[20, 12])
        
        # Treeview stílusok
        style.configure('Modern.Treeview', 
                       background=self.colors['bg_card'], 
                       foreground=self.colors['text_dark'],
                       borderwidth=2, 
                       relief='solid',
                       font=('Segoe UI', 10))
        style.configure('Modern.Treeview.Heading', 
                       background=self.colors['border'], 
                       foreground=self.colors['text_dark'],
                       borderwidth=2, 
                       relief='solid', 
                       font=('Segoe UI', 10, 'bold'))
    
    def create_gui(self):
        """Modern GUI felület létrehozása"""
        # Fő container
        main_frame = ttk.Frame(self.root, style='Main.TFrame')
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # Modern cím
        title_frame = ttk.Frame(main_frame, style='Main.TFrame')
        title_frame.pack(fill='x', pady=(0, 30))
        
        title_label = ttk.Label(title_frame, text="Páciens email emlékeztető rendszer v2.0", 
                               style='Title.TLabel')
        title_label.pack()
        
        # Notebook (fülek)
        self.notebook = ttk.Notebook(main_frame, style='Modern.TNotebook')
        self.notebook.pack(fill='both', expand=True)
        
        # Fülek létrehozása
        self.create_settings_tab()
        self.create_patients_tab()
        self.create_calendar_tab()
        self.create_messages_tab()
        self.create_automation_tab()
        self.create_templates_tab()
        self.create_logs_tab()
    
    def create_settings_tab(self):
        """Beállítások fül"""
        settings_frame = ttk.Frame(self.notebook, style='Main.TFrame')
        self.notebook.add(settings_frame, text="Beállítások")
        
        # Email beállítások
        email_section = ttk.LabelFrame(settings_frame, text="Email beállítások", 
                                      style='Modern.TLabelframe')
        email_section.pack(fill='x', padx=15, pady=15)
        
        # SMTP szerver
        self.create_input_row(email_section, "SMTP szerver:", self.smtp_server)
        self.create_input_row(email_section, "Port:", self.smtp_port)
        self.create_input_row(email_section, "Email cím:", self.email_username)
        self.create_input_row(email_section, "Jelszó:", self.email_password, show="*")
        self.create_input_row(email_section, "Rendelő neve:", self.clinic_name)
        
        # Email gombok
        email_buttons = ttk.Frame(email_section, style='Main.TFrame')
        email_buttons.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(email_buttons, text="Teszt email", command=self.send_test_email,
                  style='Primary.TButton').pack(side='left', padx=(0, 10))
        ttk.Button(email_buttons, text="Beállítások mentése", command=self.save_email_settings,
                  style='Secondary.TButton').pack(side='right')
        
        # Google Calendar beállítások
        calendar_section = ttk.LabelFrame(settings_frame, text="Google Calendar beállítások", 
                                         style='Modern.TLabelframe')
        calendar_section.pack(fill='x', padx=15, pady=15)
        
        status_frame = ttk.Frame(calendar_section, style='Main.TFrame')
        status_frame.pack(fill='x', padx=15, pady=10)
        
        ttk.Label(status_frame, text="Állapot:", style='Modern.TLabel').pack(side='left')
        ttk.Label(status_frame, textvariable=self.calendar_status, style='Status.TLabel').pack(side='left', padx=(10, 0))
        
        calendar_buttons = ttk.Frame(calendar_section, style='Main.TFrame')
        calendar_buttons.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(calendar_buttons, text="Google Calendar kapcsolat", 
                  command=self.authenticate_google_calendar,
                  style='Primary.TButton').pack(side='left')
        ttk.Button(calendar_buttons, text="Google Console", 
                  command=self.open_google_console,
                  style='Secondary.TButton').pack(side='right')
    
    def create_input_row(self, parent, label_text, variable, show=None):
        """Input sor létrehozása"""
        row_frame = ttk.Frame(parent, style='Main.TFrame')
        row_frame.pack(fill='x', padx=15, pady=8)
        
        ttk.Label(row_frame, text=label_text, style='Modern.TLabel').pack(side='left', padx=(0, 15))
        ttk.Entry(row_frame, textvariable=variable, width=35, show=show, 
                 style='Modern.TEntry').pack(side='right')
    
    def create_patients_tab(self):
        """Páciensek fül"""
        patients_frame = ttk.Frame(self.notebook, style='Main.TFrame')
        self.notebook.add(patients_frame, text="Páciensek")
        
        # Új páciens hozzáadása section
        add_section = ttk.LabelFrame(patients_frame, text="Új páciens hozzáadása", 
                                    style='Modern.TLabelframe')
        add_section.pack(fill='x', padx=10, pady=5)
        
        # Input mezők
        self.create_input_row(add_section, "Teljes név:", self.new_patient_name)
        self.create_input_row(add_section, "Email cím:", self.new_patient_email)
        self.create_input_row(add_section, "Telefonszám:", self.new_patient_phone)
        
        # Nyelv választó
        lang_frame = ttk.Frame(add_section, style='Main.TFrame')
        lang_frame.pack(fill='x', padx=15, pady=8)
        
        ttk.Label(lang_frame, text="Preferált nyelv:", style='Modern.TLabel').pack(side='left', padx=(0, 15))
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.new_patient_language, 
                                values=['hu', 'de'], width=32, style='Modern.TCombobox', state="readonly")
        lang_combo.pack(side='right')
        
        # Hozzáadás, szerkesztés és törlés gombok
        add_button_frame = ttk.Frame(add_section, style='Main.TFrame')
        add_button_frame.pack(fill='x', padx=10, pady=5)

        # Bal oldali gombok
        left_buttons = ttk.Frame(add_button_frame, style='Main.TFrame')
        left_buttons.pack(side='left')

        ttk.Button(left_buttons, text="Páciens hozzáadása", command=self.add_patient,
                style='Primary.TButton').pack(side='left', padx=(0, 10))
        ttk.Button(left_buttons, text="Páciens szerkesztése", command=self.edit_selected_patient,
                style='Primary.TButton').pack(side='left', padx=(0, 10))
        ttk.Button(left_buttons, text="Kijelölt páciens törlése", command=self.delete_selected_patient,
                style='Danger.TButton').pack(side='left')

        # Jobb oldali gomb
        ttk.Button(add_button_frame, text="Excel importálás", command=self.import_excel,
                style='Secondary.TButton').pack(side='right')
        
        # Keresés section
        search_section = ttk.LabelFrame(patients_frame, text="Páciensek keresése", 
                                    style='Modern.TLabelframe')
        search_section.pack(fill='x', padx=10, pady=5)
        
        search_frame = ttk.Frame(search_section, style='Main.TFrame')
        search_frame.pack(fill='x', padx=10, pady=5)
        
        # Keresés változó és mező
        self.search_term = tk.StringVar()
        self.search_term.trace('w', self.filter_patients)  # Automatikus szűrés
        
        ttk.Label(search_frame, text="Keresés (név vagy email):", style='Modern.TLabel').pack(side='left', padx=(0, 10))
        search_entry = ttk.Entry(search_frame, textvariable=self.search_term, width=30, 
                                style='Modern.TEntry')
        search_entry.pack(side='left', padx=(0, 10))
        
        ttk.Button(search_frame, text="Keresés törlése", command=self.clear_search,
                style='Primary.TButton').pack(side='left', padx=5)
        
        # Páciensek lista section
        list_section = ttk.LabelFrame(patients_frame, text="Páciensek listája (CTRL + Space több páciens kijelölése)", 
                                    style='Modern.TLabelframe')
        list_section.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Treeview container
        tree_container = ttk.Frame(list_section, style='Main.TFrame')
        tree_container.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Treeview
        columns = ('ID', 'Email', 'Telefon', 'Nyelv', 'Regisztráció')
        self.patients_tree = ttk.Treeview(tree_container, columns=columns, show='tree headings', 
                                        style='Modern.Treeview', height=12)
        
        # Fejlécek
        self.patients_tree.heading('#0', text='Név')
        self.patients_tree.heading('ID', text='ID')
        self.patients_tree.heading('Email', text='Email')
        self.patients_tree.heading('Telefon', text='Telefon')
        self.patients_tree.heading('Nyelv', text='Nyelv')
        self.patients_tree.heading('Regisztráció', text='Regisztráció')
        
        # Oszlopok szélessége
        self.patients_tree.column('#0', width=200)
        self.patients_tree.column('ID', width=50, anchor='center')
        self.patients_tree.column('Email', width=250)
        self.patients_tree.column('Telefon', width=150)
        self.patients_tree.column('Nyelv', width=80, anchor='center')
        self.patients_tree.column('Regisztráció', width=120)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_container, orient='vertical', command=self.patients_tree.yview)
        self.patients_tree.configure(yscrollcommand=scrollbar.set)
        
        self.patients_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Műveletek 
        actions_frame = ttk.Frame(list_section, style='Main.TFrame')
        actions_frame.pack(fill='x', padx=15, pady=15)
        
        # Bal oldali gombok
        left_buttons = ttk.Frame(actions_frame, style='Main.TFrame')
        left_buttons.pack(side='left')
        
        ttk.Button(left_buttons, text="Lista frissítése", command=self.refresh_patients_list,
                style='Secondary.TButton').pack(side='left', padx=5)
        ttk.Button(left_buttons, text="Email küldése", command=self.send_email_to_patient,
                style='Primary.TButton').pack(side='left', padx=5)
        
        # Páciensek betöltése
        self.refresh_patients_list()
    
    def create_calendar_tab(self):
        """Naptár fül - 30 napos előretekintés"""
        calendar_frame = ttk.Frame(self.notebook, style='Main.TFrame')
        self.notebook.add(calendar_frame, text="Naptár")
        
        # Vezérlő gombok
        control_section = ttk.LabelFrame(calendar_frame, text="Naptár műveletek", 
                                        style='Modern.TLabelframe')
        control_section.pack(fill='x', padx=15, pady=15)
        
        control_buttons = ttk.Frame(control_section, style='Main.TFrame')
        control_buttons.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(control_buttons, text="Naptár szinkronizálás", command=self.sync_calendar,
                style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(control_buttons, text="Események frissítése", command=self.refresh_calendar_events,
                style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(control_buttons, text="Esemény hozzáadása", command=self.add_manual_calendar_event,
                style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(control_buttons, text="Kijelölt esemény törlése", command=self.delete_selected_event,
                style='Danger.TButton').pack(side='left', padx=5)
        ttk.Button(control_buttons, text="Emlékeztetők küldése", command=self.send_calendar_reminders,
                style='Success.TButton').pack(side='right')
        
        # Események lista
        events_section = ttk.LabelFrame(calendar_frame, text="Következő 30 nap eseményei", 
                                    style='Modern.TLabelframe')
        events_section.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Treeview container
        events_container = ttk.Frame(events_section, style='Main.TFrame')
        events_container.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Events treeview - ID oszlop hozzáadása a törléshez
        event_columns = ('ID', 'Dátum', 'Idő', 'Páciens Email', 'Esemény', 'Leírás', 'Emlékeztető')
        self.events_tree = ttk.Treeview(events_container, columns=event_columns, show='headings', 
                                    style='Modern.Treeview', height=15)
        
        # Fejlécek beállítása
        self.events_tree.heading('ID', text='ID')
        self.events_tree.heading('Dátum', text='Dátum')
        self.events_tree.heading('Idő', text='Idő')
        self.events_tree.heading('Páciens Email', text='Páciens Email')
        self.events_tree.heading('Esemény', text='Esemény')
        self.events_tree.heading('Leírás', text='Leírás')
        self.events_tree.heading('Emlékeztető', text='Emlékeztető')
        
        # Oszlopok szélessége - ID oszlop elrejtése
        self.events_tree.column('ID', width=0, stretch=False)  # Elrejtjük az ID oszlopot
        self.events_tree.column('Dátum', width=100, anchor='center')
        self.events_tree.column('Idő', width=80, anchor='center')
        self.events_tree.column('Páciens Email', width=200)
        self.events_tree.column('Esemény', width=200)
        self.events_tree.column('Leírás', width=250)
        self.events_tree.column('Emlékeztető', width=100, anchor='center')
        
        # ID fejléc elrejtése
        self.events_tree.heading('ID', text='')
        
        # Scrollbar
        events_scrollbar = ttk.Scrollbar(events_container, orient='vertical', command=self.events_tree.yview)
        self.events_tree.configure(yscrollcommand=events_scrollbar.set)
        
        self.events_tree.pack(side='left', fill='both', expand=True)
        events_scrollbar.pack(side='right', fill='y')
        
        # Események betöltése
        self.refresh_calendar_events()
    
    def create_messages_tab(self):
        """Azonnali üzenetek fül"""
        messages_frame = ttk.Frame(self.notebook, style='Main.TFrame')
        self.notebook.add(messages_frame, text="Azonnali üzenetek")
        
        # Üzenet összeállítás section
        compose_section = ttk.LabelFrame(messages_frame, text="Üzenet összeállítása", 
                                        style='Modern.TLabelframe')
        compose_section.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Címzettek választása
        recipients_frame = ttk.Frame(compose_section, style='Main.TFrame')
        recipients_frame.pack(fill='x', padx=15, pady=10)
        
        ttk.Label(recipients_frame, text="Címzettek:", style='Modern.TLabel').pack(side='left')
        recipients_combo = ttk.Combobox(recipients_frame, textvariable=self.message_recipients, 
                                    values=['all', 'selected'], width=20, 
                                    style='Modern.TCombobox', state="readonly")
        recipients_combo.pack(side='left', padx=10)
        recipients_combo.set('all')
        
        info_label = ttk.Label(recipients_frame, text="('all' = minden páciens, 'selected' = kijelölt páciensek)", 
                            style='Modern.TLabel')
        info_label.pack(side='left', padx=10)
        
        # Frissítés gomb hozzáadása
        ttk.Button(recipients_frame, text="Kijelölés frissítése", 
                command=self.refresh_selected_patients,
                style='Secondary.TButton').pack(side='right')
        
        # Tárgy mező
        self.create_input_row(compose_section, "Email tárgy:", self.message_subject)
        
        # Üzenet törzs
        body_frame = ttk.Frame(compose_section, style='Main.TFrame')
        body_frame.pack(fill='both', expand=True, padx=15, pady=10)
        
        ttk.Label(body_frame, text="Üzenet tartalma:", style='Modern.TLabel').pack(anchor='w')
        
        self.message_body = scrolledtext.ScrolledText(body_frame, height=5, width=80, 
                                                    font=('Segoe UI', 10))
        self.message_body.pack(fill='both', expand=True, pady=5)
        
        # Gyors sablonok
        templates_frame = ttk.Frame(compose_section, style='Main.TFrame')
        templates_frame.pack(fill='x', padx=15, pady=10)
        
        ttk.Label(templates_frame, text="Gyors sablonok:", style='Modern.TLabel').pack(side='left')
        
        template_buttons = [
            ("Időpont változás", self.load_appointment_change_template),
            ("Rendelő információ", self.load_clinic_info_template),
            ("Sürgős értesítés", self.load_urgent_template),
            ("Ünnepek", self.load_holiday_template)
        ]
        
        for text, command in template_buttons:
            ttk.Button(templates_frame, text=text, command=command,
                    style='Primary.TButton').pack(side='left', padx=5)
        
        # Küldés gombok
        send_frame = ttk.Frame(compose_section, style='Main.TFrame')
        send_frame.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(send_frame, text="Azonnali küldés", command=self.send_immediate_message,
                style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(send_frame, text="Előnézet", command=self.preview_message,
                style='Secondary.TButton').pack(side='left', padx=5)
        ttk.Button(send_frame, text="Törlés", command=self.clear_message,
                style='Danger.TButton').pack(side='right')
        
        # Kijelölt páciensek lista
        selected_section = ttk.LabelFrame(messages_frame, text="Kijelölt páciensek", 
                                        style='Modern.TLabelframe')
        selected_section.pack(fill='x', padx=15, pady=15)
        
        # Container frame a listbox-nak
        selected_container = ttk.Frame(selected_section, style='Main.TFrame')
        selected_container.pack(fill='both', expand=True, padx=15, pady=10)
        
        # Listbox a kijelölt páciensek megjelenítésére
        self.selected_patients_listbox = tk.Listbox(selected_container, 
                                                height=6, 
                                                font=('Segoe UI', 10),
                                                bg=self.colors['bg_card'],
                                                fg=self.colors['text_dark'],
                                                selectbackground=self.colors['primary'],
                                                selectforeground='white',
                                                borderwidth=2,
                                                relief='solid')
        self.selected_patients_listbox.pack(side='left', fill='both', expand=True)
        
        # Scrollbar a listbox-hoz
        selected_scrollbar = ttk.Scrollbar(selected_container, orient='vertical', 
                                        command=self.selected_patients_listbox.yview)
        self.selected_patients_listbox.configure(yscrollcommand=selected_scrollbar.set)
        selected_scrollbar.pack(side='right', fill='y')
        
        # Kezdeti tartalom betöltése
        self.refresh_selected_patients()
    
    def create_automation_tab(self):
        """Automatizálás fül"""
        automation_frame = ttk.Frame(self.notebook, style='Main.TFrame')
        self.notebook.add(automation_frame, text="Automatizálás")
        
        # Státusz section
        status_section = ttk.LabelFrame(automation_frame, text="Rendszer állapota", 
                                       style='Modern.TLabelframe')
        status_section.pack(fill='x', padx=15, pady=15)
        
        status_frame = ttk.Frame(status_section, style='Main.TFrame')
        status_frame.pack(fill='x', padx=15, pady=15)
        
        ttk.Label(status_frame, text="Automatizálás:", style='Modern.TLabel').pack(side='left')
        ttk.Label(status_frame, textvariable=self.automation_status, style='Status.TLabel').pack(side='left', padx=15)
        
        # Vezérlés gombok
        control_frame = ttk.Frame(status_section, style='Main.TFrame')
        control_frame.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(control_frame, text="Automatizálás indítása", command=self.start_automation,
                  style='Secondary.TButton').pack(side='left', padx=5)
        ttk.Button(control_frame, text="Automatizálás leállítása", command=self.stop_automation,
                  style='Danger.TButton').pack(side='right', padx=5)
        
        # Ütemezés beállítások section
        schedule_section = ttk.LabelFrame(automation_frame, text="Ütemezés beállítások", 
                                         style='Modern.TLabelframe')
        schedule_section.pack(fill='x', padx=15, pady=15)
        
        schedule_frame = ttk.Frame(schedule_section, style='Main.TFrame')
        schedule_frame.pack(fill='x', padx=15, pady=15)
        
        schedule_text = """Optimalizált automatizálás működése:

• EMLÉKEZTETŐK: Naponta egyszer 12:00-kor
  → Holnapi időpontokra emlékeztető emailek küldése

• ÚJ IDŐPONTOK: Naponta egyszer 15:30-kor  
  → Mai napon létrehozott új időpontok visszaigazolása

• Automatikus páciens felismerés Google Calendar alapján
• Nyelv-specifikus email sablonok használata"""
        
        ttk.Label(schedule_frame, text=schedule_text, style='Modern.TLabel', 
                 justify='left').pack(anchor='w')
        
        # Manuális műveletek section
        manual_section = ttk.LabelFrame(automation_frame, text="Manuális műveletek", 
                                       style='Modern.TLabelframe')
        manual_section.pack(fill='x', padx=15, pady=15)
        
        manual_frame = ttk.Frame(manual_section, style='Main.TFrame')
        manual_frame.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(manual_frame, text="Azonnali emlékeztetők", 
                  command=self.send_immediate_reminders,
                  style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(manual_frame, text="Új időpontok értesítése", 
                  command=self.send_new_appointment_notifications,
                  style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(manual_frame, text="Calendar szinkronizálás", command=self.sync_calendar,
                  style='Secondary.TButton').pack(side='right', padx=5)
    
    def create_templates_tab(self):
        """Email sablonok fül"""
        templates_frame = ttk.Frame(self.notebook, style='Main.TFrame')
        self.notebook.add(templates_frame, text="Email sablonok")
        
        # Sablon szerkesztő section
        edit_section = ttk.LabelFrame(templates_frame, text="Sablon szerkesztő", 
                                     style='Modern.TLabelframe')
        edit_section.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Sablon kiválasztás
        selector_frame = ttk.Frame(edit_section, style='Main.TFrame')
        selector_frame.pack(fill='x', padx=15, pady=10)
        
        ttk.Label(selector_frame, text="Email típus:", style='Modern.TLabel').pack(side='left')
        self.template_type = tk.StringVar(value='reminder')
        type_combo = ttk.Combobox(selector_frame, textvariable=self.template_type, 
                                 values=['reminder', 'confirmation'], width=15, 
                                 style='Modern.TCombobox', state="readonly")
        type_combo.pack(side='left', padx=10)
        
        ttk.Label(selector_frame, text="Nyelv:", style='Modern.TLabel').pack(side='left', padx=(20, 0))
        self.template_language = tk.StringVar(value='hu')
        lang_combo = ttk.Combobox(selector_frame, textvariable=self.template_language, 
                                 values=['hu', 'de'], width=10, 
                                 style='Modern.TCombobox', state="readonly")
        lang_combo.pack(side='left', padx=10)
        
        ttk.Button(selector_frame, text="Sablon betöltése", command=self.load_template,
                  style='Secondary.TButton').pack(side='right')
        
        # Sablon mezők
        self.template_subject = tk.StringVar()
        self.create_input_row(edit_section, "Email tárgy:", self.template_subject)
        
        # Email törzs
        body_frame = ttk.Frame(edit_section, style='Main.TFrame')
        body_frame.pack(fill='both', expand=True, padx=15, pady=10)
        
        ttk.Label(body_frame, text="Email törzs:", style='Modern.TLabel').pack(anchor='w')
        
        self.template_body = scrolledtext.ScrolledText(body_frame, height=10, width=80, 
                                                      font=('Segoe UI', 10))
        self.template_body.pack(fill='both', expand=True, pady=5)
        
        # Mentés gomb
        save_template_frame = ttk.Frame(edit_section, style='Main.TFrame')
        save_template_frame.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(save_template_frame, text="Sablon mentése", command=self.save_template,
                  style='Primary.TButton').pack(side='right')
        
        # Sablon változók info
        info_section = ttk.LabelFrame(templates_frame, text="Használható változók", 
                                     style='Modern.TLabelframe')
        info_section.pack(fill='x', padx=15, pady=15)
        
        info_frame = ttk.Frame(info_section, style='Main.TFrame')
        info_frame.pack(fill='x', padx=15, pady=10)
        
        info_text = """Használható változók az email sablonokban:
• {patient_name} - Páciens neve
• {appointment_date} - Időpont dátuma
• {appointment_time} - Időpont ideje
• {clinic_name} - Rendelő neve"""
        
        ttk.Label(info_frame, text=info_text, style='Modern.TLabel', 
                 justify='left').pack(anchor='w')
    
    def create_logs_tab(self):
        """Naplók fül"""
        logs_frame = ttk.Frame(self.notebook, style='Main.TFrame')
        self.notebook.add(logs_frame, text="Naplók")
        
        # Felső panel
        top_frame = ttk.Frame(logs_frame, style='Main.TFrame')
        top_frame.pack(fill='x', padx=15, pady=15)
        
        ttk.Button(top_frame, text="Naplók frissítése", command=self.refresh_logs,
                  style='Secondary.TButton').pack(side='left')
        ttk.Button(top_frame, text="Naplók törlése", command=self.clear_logs,
                  style='Danger.TButton').pack(side='right')
        
        # Naplók listája
        logs_list_frame = ttk.Frame(logs_frame, style='Main.TFrame')
        logs_list_frame.pack(fill='both', expand=True, padx=15, pady=15)
        
        # Treeview
        log_columns = ('Időpont', 'Szint', 'Üzenet', 'Páciens Email')
        self.logs_tree = ttk.Treeview(logs_list_frame, columns=log_columns, show='headings', 
                                     style='Modern.Treeview', height=20)
        
        for col in log_columns:
            self.logs_tree.heading(col, text=col)
            if col == 'Időpont':
                self.logs_tree.column(col, width=150)
            elif col == 'Szint':
                self.logs_tree.column(col, width=80, anchor='center')
            elif col == 'Üzenet':
                self.logs_tree.column(col, width=400)
            else:
                self.logs_tree.column(col, width=200)
        
        # Scrollbar
        logs_scrollbar = ttk.Scrollbar(logs_list_frame, orient='vertical', command=self.logs_tree.yview)
        self.logs_tree.configure(yscrollcommand=logs_scrollbar.set)
        
        self.logs_tree.pack(side='left', fill='both', expand=True)
        logs_scrollbar.pack(side='right', fill='y')
        
        # Naplók betöltése
        self.refresh_logs()

    # Event handlers és egyéb methods
    def save_email_settings(self):
        """Email beállítások mentése"""
        try:
            self.config_manager.set_email_config(
                self.smtp_server.get(),
                int(self.smtp_port.get()),
                self.email_username.get(),
                self.email_password.get(),
                self.clinic_name.get()
            )
            messagebox.showinfo("Siker", "Beállítások sikeresen mentve!")
        except Exception as e:
            messagebox.showerror("Hiba", f"Beállítások mentési hiba: {str(e)}")
    
    def send_test_email(self):
        """Teszt email küldése"""
        try:
            success, message = self.email_manager.send_test_email()
            
            if success:
                messagebox.showinfo("Siker", "Teszt email elküldve!")
            else:
                messagebox.showerror("Hiba", f"Teszt email hiba: {message}")
        except Exception as e:
            messagebox.showerror("Hiba", f"Email küldési hiba: {str(e)}")
    
    def authenticate_google_calendar(self):
        """Google Calendar authentikáció"""
        if not GOOGLE_API_AVAILABLE:
            messagebox.showerror("Hiba", "Google API kliens nincs telepítve!\npip install google-api-python-client google-auth-oauthlib")
            return
        
        try:
            if not self.calendar_manager:
                self.calendar_manager = GoogleCalendarManager()
            
            success = self.calendar_manager.authenticate()
            if success:
                messagebox.showinfo("Siker", "Google Calendar authentikáció sikeres!")
                self.calendar_status.set("Kapcsolódva")
                # Automatikus szinkronizálás
                self.sync_calendar()
            
        except FileNotFoundError as e:
            messagebox.showerror("Hiba", str(e))
        except Exception as e:
            messagebox.showerror("Hiba", f"Authentikációs hiba: {str(e)}")
    
    def open_google_console(self):
        """Google Cloud Console megnyitása"""
        webbrowser.open('https://console.cloud.google.com/apis/credentials')
    
    def add_patient(self):
        """Páciens hozzáadása"""
        name = self.new_patient_name.get().strip()
        email = self.new_patient_email.get().strip()
        phone = self.new_patient_phone.get().strip()
        language = self.new_patient_language.get()
        
        if not name or not email:
            messagebox.showerror("Hiba", "Név és email cím megadása kötelező!")
            return
        
        try:
            patient_id = self.db_manager.add_patient(name, email, phone, language)
            self.db_manager.add_log("INFO", f"Új páciens hozzáadva: {name} ({email})")
            
            # Mezők törlése
            self.new_patient_name.set("")
            self.new_patient_email.set("")
            self.new_patient_phone.set("")
            self.new_patient_language.set("hu")
            
            # Lista frissítése
            self.refresh_patients_list()
            
            messagebox.showinfo("Siker", f"Páciens hozzáadva! ID: {patient_id}")
            
        except ValueError as e:
            messagebox.showerror("Hiba", str(e))
        except Exception as e:
            messagebox.showerror("Hiba", f"Páciens hozzáadási hiba: {str(e)}")
    
    def delete_selected_patient(self):
        """Kijelölt páciens törlése"""
        selection = self.patients_tree.selection()
        if not selection:
            messagebox.showwarning("Figyelmeztetés", "Válasszon ki egy pácienst a törléshez!")
            return
        
        item = self.patients_tree.item(selection[0])
        patient_id = item['values'][0]
        patient_name = item['text']
        patient_email = item['values'][1]
        
        # Megerősítés
        confirm_message = f"""Biztos törli ezt a pácienst?

Név: {patient_name}
Email: {patient_email}

Ez a művelet nem vonható vissza!"""
        
        if messagebox.askyesno("Páciens törlése", confirm_message):
            try:
                success = self.db_manager.delete_patient(patient_id)
                
                if success:
                    self.db_manager.add_log("INFO", f"Páciens törölve: {patient_name} ({patient_email})")
                    self.refresh_patients_list()
                    messagebox.showinfo("Siker", "Páciens sikeresen törölve!")
                else:
                    messagebox.showerror("Hiba", "Páciens törlése sikertelen!")
            
            except Exception as e:
                messagebox.showerror("Hiba", f"Törlési hiba: {str(e)}")
                self.db_manager.add_log("ERROR", f"Törlési hiba: {str(e)}")
    
    def edit_selected_patient(self):
        """Kijelölt páciens szerkesztése"""
        selection = self.patients_tree.selection()
        if not selection:
            messagebox.showwarning("Figyelmeztetés", "Válasszon ki egy pácienst a szerkesztéshez!")
            return
        
        item = self.patients_tree.item(selection[0])
        patient_id = item['values'][0]
        patient_name = item['text']
        patient_email = item['values'][1]
        patient_phone = item['values'][2]
        patient_language = item['values'][3].lower()
        
        # Szerkesztő ablak
        edit_window = tk.Toplevel(self.root)
        edit_window.title(f"Páciens szerkesztése - {patient_name}")
        edit_window.geometry("500x600")
        edit_window.configure(bg=self.colors['bg_main'])
        edit_window.resizable(False, False)
        
        # Központosítás
        edit_window.transient(self.root)
        edit_window.grab_set()
        
        # Cím
        title_frame = ttk.Frame(edit_window, style='Main.TFrame')
        title_frame.pack(fill='x', padx=20, pady=20)
        
        ttk.Label(title_frame, text="Páciens adatok szerkesztése", 
                style='Title.TLabel').pack()
        
        # Adatok frame
        data_frame = ttk.LabelFrame(edit_window, text="Páciens adatok", 
                                style='Modern.TLabelframe')
        data_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # Változók a szerkesztéshez
        edit_name = tk.StringVar(value=patient_name)
        edit_email = tk.StringVar(value=patient_email)
        edit_phone = tk.StringVar(value=patient_phone)
        edit_language = tk.StringVar(value=patient_language)
        
        # Input mezők
        def create_edit_row(parent, label_text, variable, **kwargs):
            row_frame = ttk.Frame(parent, style='Main.TFrame')
            row_frame.pack(fill='x', padx=15, pady=8)
            
            ttk.Label(row_frame, text=label_text, style='Modern.TLabel').pack(side='left', padx=(0, 15))
            entry = ttk.Entry(row_frame, textvariable=variable, width=35, 
                            style='Modern.TEntry', **kwargs)
            entry.pack(side='right')
            return entry
        
        create_edit_row(data_frame, "Teljes név:", edit_name)
        create_edit_row(data_frame, "Email cím:", edit_email)
        create_edit_row(data_frame, "Telefonszám:", edit_phone)
        
        # Nyelv választó
        lang_frame = ttk.Frame(data_frame, style='Main.TFrame')
        lang_frame.pack(fill='x', padx=15, pady=8)
        
        ttk.Label(lang_frame, text="Preferált nyelv:", style='Modern.TLabel').pack(side='left', padx=(0, 15))
        lang_combo = ttk.Combobox(lang_frame, textvariable=edit_language, 
                                values=['hu', 'de'], width=32, style='Modern.TCombobox', state="readonly")
        lang_combo.pack(side='right')
        
        def save_changes():
            """Változások mentése"""
            try:
                new_name = edit_name.get().strip()
                new_email = edit_email.get().strip()
                new_phone = edit_phone.get().strip()
                new_language = edit_language.get()
                
                if not new_name or not new_email:
                    messagebox.showerror("Hiba", "Név és email cím megadása kötelező!")
                    return
                
                # Adatbázis frissítése
                conn = sqlite3.connect(self.db_manager.db_name)
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE patients 
                    SET name = ?, email = ?, phone = ?, language = ? 
                    WHERE id = ?
                ''', (new_name, new_email, new_phone, new_language, patient_id))
                
                conn.commit()
                conn.close()
                
                # Log
                self.db_manager.add_log("INFO", f"Páciens módosítva: {new_name} (ID: {patient_id})")
                
                # Lista frissítése
                self.refresh_patients_list()
                
                messagebox.showinfo("Siker", "Páciens adatok sikeresen frissítve!")
                edit_window.destroy()
                
            except sqlite3.IntegrityError:
                messagebox.showerror("Hiba", "Ez az email cím már létezik!")
            except Exception as e:
                messagebox.showerror("Hiba", f"Módosítási hiba: {str(e)}")
        
        # Gombok
        button_frame = ttk.Frame(edit_window, style='Main.TFrame')
        button_frame.pack(fill='x', padx=20, pady=20)
        
        ttk.Button(button_frame, text="Mentés", command=save_changes,
                style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(button_frame, text="Mégse", command=edit_window.destroy,
                style='Secondary.TButton').pack(side='right', padx=5)
    
    def filter_patients(self, *args):
        """Páciensek szűrése keresési kifejezés alapján"""
        search_term = self.search_term.get().lower().strip()
        
        # Jelenlegi elemek törlése
        for item in self.patients_tree.get_children():
            self.patients_tree.delete(item)
        
        # Ha nincs keresési kifejezés, minden pácienst megjelenít
        if not search_term:
            for patient in self.all_patients:
                created_date = patient[5][:10] if len(patient[5]) > 10 else patient[5]
                self.patients_tree.insert('', 'end', text=patient[1], values=(
                    patient[0],  # ID
                    patient[2],  # Email
                    patient[3] or '',  # Telefon
                    patient[4].upper(),  # Nyelv
                    created_date  # Regisztráció dátuma
                ))
            return
        
        # Szűrt páciensek megjelenítése
        for patient in self.all_patients:
            patient_name = patient[1].lower()
            patient_email = patient[2].lower()
            
            if search_term in patient_name or search_term in patient_email:
                created_date = patient[5][:10] if len(patient[5]) > 10 else patient[5]
                self.patients_tree.insert('', 'end', text=patient[1], values=(
                    patient[0],  # ID
                    patient[2],  # Email
                    patient[3] or '',  # Telefon
                    patient[4].upper(),  # Nyelv
                    created_date  # Regisztráció dátuma
                ))

    def clear_search(self):
        """Keresés törlése"""
        self.search_term.set("")

    def refresh_patients_list(self):
        """Páciensek lista frissítése"""
        # Jelenlegi elemek törlése
        for item in self.patients_tree.get_children():
            self.patients_tree.delete(item)
        
        # Páciensek betöltése és tárolása
        self.all_patients = self.db_manager.get_patients()
        
        for patient in self.all_patients:
            # patient: (id, name, email, phone, language, created_at, active)
            created_date = patient[5][:10] if len(patient[5]) > 10 else patient[5]  # Csak dátum
            
            self.patients_tree.insert('', 'end', text=patient[1], values=(
                patient[0],  # ID
                patient[2],  # Email
                patient[3] or '',  # Telefon
                patient[4].upper(),  # Nyelv
                created_date  # Regisztráció dátuma
            ))

    def send_email_to_patient(self):
        """Email küldése páciensnek"""
        selection = self.patients_tree.selection()
        if not selection:
            messagebox.showwarning("Figyelmeztetés", "Válasszon ki egy pácienst!")
            return
        
        item = self.patients_tree.item(selection[0])
        patient_email = item['values'][1]
        patient_name = item['text']
        
        # Egyszerű email küldő ablak
        email_window = tk.Toplevel(self.root)
        email_window.title(f"Email küldése - {patient_name}")
        email_window.geometry("500x400")
        email_window.configure(bg=self.colors['bg_main'])
        
        ttk.Label(email_window, text=f"Címzett: {patient_name} ({patient_email})",
                 style='Modern.TLabel').pack(pady=10)
        
        ttk.Label(email_window, text="Tárgy:", style='Modern.TLabel').pack(anchor='w', padx=10)
        subject_var = tk.StringVar(value="Üzenet a rendelőből")
        ttk.Entry(email_window, textvariable=subject_var, width=60, 
                 style='Modern.TEntry').pack(padx=10, pady=5)
        
        ttk.Label(email_window, text="Üzenet:", style='Modern.TLabel').pack(anchor='w', padx=10)
        message_text = scrolledtext.ScrolledText(email_window, height=15, width=60)
        message_text.pack(padx=10, pady=5, fill='both', expand=True)
        
        def send_custom_email():
            subject = subject_var.get()
            body = message_text.get('1.0', tk.END).strip()
            
            if not subject or not body:
                messagebox.showerror("Hiba", "Tárgy és üzenet megadása kötelező!")
                return
            
            success, message = self.email_manager.send_email(patient_email, subject, body, patient_name)
            
            if success:
                messagebox.showinfo("Siker", "Email elküldve!")
                self.db_manager.add_log("INFO", f"Egyedi email elküldve: {patient_name}", patient_email)
                email_window.destroy()
            else:
                messagebox.showerror("Hiba", f"Email küldési hiba: {message}")
        
        ttk.Button(email_window, text="Email küldése", command=send_custom_email,
                  style='Primary.TButton').pack(pady=10)
    
    def import_excel(self):
        """Excel fájl importálása"""
        file_path = filedialog.askopenfilename(
            title="Excel fájl kiválasztása",
            filetypes=[("Excel fájlok", "*.xlsx *.xls")]
        )
        
        if not file_path:
            return
        
        try:
            # Excel fájl beolvasása
            df = pd.read_excel(file_path)
            
            # Oszlopok ellenőrzése
            required_columns = ['név', 'email']
            missing_columns = [col for col in required_columns if col.lower() not in [c.lower() for c in df.columns]]
            
            if missing_columns:
                messagebox.showerror("Hiba", f"Hiányzó oszlopok: {', '.join(missing_columns)}")
                return
            
            # Oszlopok normalizálása (kis betűs)
            df.columns = df.columns.str.lower()
            
            # Páciensek importálása
            imported_count = 0
            error_count = 0
            
            for index, row in df.iterrows():
                try:
                    name = str(row['név']).strip()
                    email = str(row['email']).strip()
                    phone = str(row.get('telefon', '')).strip()
                    language = str(row.get('nyelv', 'hu')).strip()
                    
                    if name and email and '@' in email:
                        self.db_manager.add_patient(name, email, phone, language)
                        imported_count += 1
                    else:
                        error_count += 1
                
                except Exception as e:
                    error_count += 1
                    print(f"Import hiba {index}. sor: {str(e)}")
            
            self.refresh_patients_list()
            self.db_manager.add_log("INFO", f"Excel import: {imported_count} sikeres, {error_count} hiba")
            
            messagebox.showinfo("Import befejezve", 
                              f"Importálva: {imported_count} páciens\nHibák: {error_count}")
        
        except Exception as e:
            messagebox.showerror("Hiba", f"Excel import hiba: {str(e)}")
    
    def sync_calendar(self):
        """Google Calendar szinkronizálás"""
        try:
            if not self.calendar_manager:
                messagebox.showerror("Hiba", "Google Calendar nincs beállítva!")
                return
            
            events = self.calendar_manager.get_upcoming_events(days_ahead=30)
            synced_count = 0
            
            for event in events:
                try:
                    # Esemény adatok kinyerése
                    event_id = event.get('id', '')
                    summary = event.get('summary', 'Ismeretlen esemény')
                    description = event.get('description', '')
                    
                    # Időpont feldolgozás
                    start = event.get('start', {})
                    end = event.get('end', {})
                    
                    start_time_str = start.get('dateTime', start.get('date'))
                    end_time_str = end.get('dateTime', end.get('date'))
                    
                    if start_time_str and end_time_str:
                        # ISO formátum konvertálása
                        if 'T' in start_time_str:
                            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00')).replace(tzinfo=None)
                            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00')).replace(tzinfo=None)
                        else:
                            start_time = datetime.strptime(start_time_str, '%Y-%m-%d')
                            end_time = datetime.strptime(end_time_str, '%Y-%m-%d')
                        
                        # Páciens email keresése
                        patient_email = self.calendar_manager.parse_event_for_patient(event)
                        
                        # Esemény mentése adatbázisba
                        success = self.db_manager.add_calendar_event(
                            event_id, patient_email, summary, description, 
                            start_time.strftime('%Y-%m-%d %H:%M:%S'),
                            end_time.strftime('%Y-%m-%d %H:%M:%S')
                        )
                        
                        if success:
                            synced_count += 1
                
                except Exception as e:
                    print(f"Esemény szinkronizálási hiba: {str(e)}")
                    continue
            
            self.db_manager.add_log("INFO", f"Calendar szinkronizálás: {synced_count} esemény")
            self.refresh_calendar_events()
            messagebox.showinfo("Siker", f"Szinkronizálás befejezve!\n{synced_count} esemény frissítve.")
            
        except Exception as e:
            messagebox.showerror("Hiba", f"Szinkronizálási hiba: {str(e)}")
    
    def refresh_calendar_events(self):
        """Naptár események frissítése"""
        # Jelenlegi elemek törlése
        for item in self.events_tree.get_children():
            self.events_tree.delete(item)
        
        # Események betöltése
        events = self.db_manager.get_calendar_events(days_ahead=30)
        
        for event in events:
            # event: (id, google_event_id, patient_email, event_title, event_description, start_time, end_time, created_at, reminder_sent, is_new_appointment, new_appointment_notified)
            try:
                start_time = datetime.strptime(event[5], '%Y-%m-%d %H:%M:%S')
                event_date = start_time.strftime('%Y-%m-%d')
                event_time = start_time.strftime('%H:%M')
                
                reminder_status = "Elküldve" if event[8] else "Nincs"
                
                self.events_tree.insert('', 'end', values=(
                    event[0],          # ID (elrejtett)
                    event_date,        # Dátum
                    event_time,        # Idő
                    event[2] or 'Ismeretlen',  # Páciens Email
                    event[3],          # Esemény
                    event[4] or '',    # Leírás
                    reminder_status    # Emlékeztető
                ))
            except Exception as e:
                print(f"Esemény megjelenítési hiba: {str(e)}")
                continue
    
    def add_manual_calendar_event(self):
        """Manuális naptár esemény hozzáadása"""
        # Ellenőrizzük, hogy van-e kijelölt páciens
        selection = self.patients_tree.selection()
        if not selection:
            messagebox.showwarning("Figyelmeztetés", 
                                "Először jelöljön ki egy pácienst a 'Páciensek' fülön, majd próbálja újra!")
            return
        
        # Kijelölt páciens adatainak lekérése
        item = self.patients_tree.item(selection[0])
        selected_patient_name = item['text']
        selected_patient_email = item['values'][1]
        
        # Új ablak létrehozása
        event_window = tk.Toplevel(self.root)
        event_window.title(f"Új esemény - {selected_patient_name}")
        event_window.geometry("500x700")
        event_window.configure(bg=self.colors['bg_main'])
        event_window.resizable(False, False)
        
        # Központosítás
        event_window.transient(self.root)
        event_window.grab_set()
        
        # Cím
        title_frame = ttk.Frame(event_window, style='Main.TFrame')
        title_frame.pack(fill='x', padx=20, pady=20)
        
        ttk.Label(title_frame, text="Új naptár esemény", 
                style='Title.TLabel').pack()
        
        # Kijelölt páciens információ
        info_frame = ttk.Frame(event_window, style='Main.TFrame')
        info_frame.pack(fill='x', padx=20, pady=10)
        
        ttk.Label(info_frame, text=f"Kijelölt páciens: {selected_patient_name}", 
                style='Status.TLabel').pack()
        ttk.Label(info_frame, text=f"E-mail: {selected_patient_email}", 
                style='Modern.TLabel').pack()
        
        # Adatok frame
        data_frame = ttk.LabelFrame(event_window, text="Esemény adatok", 
                                style='Modern.TLabelframe')
        data_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # Változók
        event_title = tk.StringVar(value=f"Időpont - {selected_patient_name}")
        event_description = tk.StringVar()
        event_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        event_time = tk.StringVar(value="10:00")
        duration_minutes = tk.StringVar(value="30")
        
        # Input mezők
        def create_event_row(parent, label_text, variable, **kwargs):
            row_frame = ttk.Frame(parent, style='Main.TFrame')
            row_frame.pack(fill='x', padx=15, pady=8)
            
            ttk.Label(row_frame, text=label_text, style='Modern.TLabel').pack(side='left', padx=(0, 15))
            entry = ttk.Entry(row_frame, textvariable=variable, width=35, 
                            style='Modern.TEntry', **kwargs)
            entry.pack(side='right')
            return entry
        
        create_event_row(data_frame, "Esemény címe:", event_title)
        create_event_row(data_frame, "Leírás:", event_description)
        create_event_row(data_frame, "Dátum (YYYY-MM-DD):", event_date)
        create_event_row(data_frame, "Kezdés ideje (HH:MM):", event_time)
        create_event_row(data_frame, "Időtartam (perc):", duration_minutes)
        
        def save_event():
            """Esemény mentése"""
            try:
                title = event_title.get().strip()
                description = event_description.get().strip()
                date = event_date.get().strip()
                time = event_time.get().strip()
                duration = int(duration_minutes.get())
                
                if not title or not date or not time:
                    messagebox.showerror("Hiba", "Minden kötelező mező kitöltendő!")
                    return
                
                # Dátum és idő összeállítása
                start_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
                end_datetime = start_datetime + timedelta(minutes=duration)
                
                # Egyedi Google Event ID generálása
                import uuid
                google_event_id = f"manual_{uuid.uuid4().hex[:16]}"
                
                # Esemény mentése adatbázisba (automatikusan a kijelölt páciens e-mailjével)
                success = self.db_manager.add_calendar_event(
                    google_event_id, selected_patient_email, title, description,
                    start_datetime.strftime('%Y-%m-%d %H:%M:%S'),
                    end_datetime.strftime('%Y-%m-%d %H:%M:%S'),
                    is_new=True  # Új időpont jelölése
                )
                
                if success:
                    self.db_manager.add_log("INFO", f"Manuális esemény hozzáadva: {title} - {selected_patient_email}")
                    self.refresh_calendar_events()
                    messagebox.showinfo("Siker", "Esemény sikeresen hozzáadva!")
                    event_window.destroy()
                else:
                    messagebox.showerror("Hiba", "Esemény mentése sikertelen!")
                    
            except ValueError as e:
                messagebox.showerror("Hiba", f"Hibás dátum/idő formátum: {str(e)}")
            except Exception as e:
                messagebox.showerror("Hiba", f"Esemény mentési hiba: {str(e)}")
        
        # Gombok
        button_frame = ttk.Frame(event_window, style='Main.TFrame')
        button_frame.pack(fill='x', padx=20, pady=20)
        
        ttk.Button(button_frame, text="Esemény mentése", command=save_event,
                style='Primary.TButton').pack(side='left', padx=5)
        ttk.Button(button_frame, text="Mégse", command=event_window.destroy,
                style='Secondary.TButton').pack(side='right', padx=5)
    
    def delete_selected_event(self):
        """Kijelölt naptár esemény törlése"""
        selection = self.events_tree.selection()
        if not selection:
            messagebox.showwarning("Figyelmeztetés", "Válasszon ki egy eseményt a törléshez!")
            return
        
        item = self.events_tree.item(selection[0])
        event_id = item['values'][0]  # ID az első (elrejtett) oszlop
        event_title = item['values'][4]  # Esemény címe
        patient_email = item['values'][3]  # Páciens email
        event_date = item['values'][1]  # Dátum
        event_time = item['values'][2]  # Idő
        
        # Megerősítő üzenet
        confirm_message = f"""Biztos törli ezt az eseményt?

Esemény: {event_title}
Páciens: {patient_email}
Időpont: {event_date} {event_time}

FIGYELEM: Ez a művelet nem vonható vissza!"""
        
        if messagebox.askyesno("Esemény törlése", confirm_message):
            try:
                success = self.db_manager.delete_calendar_event(event_id)
                
                if success:
                    self.db_manager.add_log("INFO", f"Naptár esemény törölve: {event_title} - {patient_email}")
                    self.refresh_calendar_events()
                    messagebox.showinfo("Siker", "Esemény sikeresen törölve!")
                else:
                    messagebox.showerror("Hiba", "Esemény törlése sikertelen!")
                    
            except Exception as e:
                messagebox.showerror("Hiba", f"Törlési hiba: {str(e)}")
                self.db_manager.add_log("ERROR", f"Esemény törlési hiba: {str(e)}")
    
    def send_calendar_reminders(self):
        """Naptár események alapján emlékeztetők küldése"""
        try:
            reminders = self.db_manager.get_tomorrows_reminders()
            sent_count = 0
            
            for event in reminders:
                try:
                    patient_email = event[2]
                    if patient_email:
                        patient = self.db_manager.get_patient_by_email(patient_email)
                        
                        if patient:
                            start_time = datetime.strptime(event[5], '%Y-%m-%d %H:%M:%S')
                            appointment_date = start_time.strftime("%Y-%m-%d")
                            appointment_time = start_time.strftime("%H:%M")
                            
                            success, message = self.email_manager.send_appointment_reminder(
                                patient_email, patient[1], appointment_date, appointment_time
                            )
                            
                            if success:
                                self.db_manager.mark_reminder_sent(event[0])
                                self.db_manager.add_log("INFO", f"Naptár emlékeztető elküldve: {patient[1]}", patient_email)
                                sent_count += 1
                            else:
                                self.db_manager.add_log("ERROR", f"Emlékeztető hiba: {message}", patient_email)
                
                except Exception as e:
                    print(f"Emlékeztető küldési hiba: {str(e)}")
                    continue
            
            self.refresh_calendar_events()
            messagebox.showinfo("Befejezve", f"{sent_count} emlékeztető elküldve!")
            
        except Exception as e:
            messagebox.showerror("Hiba", f"Emlékeztető küldési hiba: {str(e)}")
    
    def send_immediate_message(self):
        """Azonnali üzenet küldése"""
        try:
            subject = self.message_subject.get().strip()
            body = self.message_body.get('1.0', tk.END).strip()
            recipients_mode = self.message_recipients.get()
            
            if not subject or not body:
                messagebox.showerror("Hiba", "Tárgy és üzenet megadása kötelező!")
                return
            
            # Kijelölt páciensek frissítése
            self.refresh_selected_patients()
            
            # Ellenőrzés selected mód esetén
            if recipients_mode == 'selected':
                selections = self.patients_tree.selection()
                if not selections:
                    messagebox.showwarning("Figyelmeztetés", 
                                         "1. Menjen a 'Páciensek' fülre\n" + 
                                         "2. Jelölje ki a kívánt pácienseket (Ctrl+klikk)\n" +
                                         "3. Térjen vissza ide")
                    return
            
            # Megerősítő üzenet személyre szabása
            if recipients_mode == 'all':
                patients_count = len(self.db_manager.get_patients())
                confirm_msg = f"Biztos elküldi az üzenetet?\n\nCímzettek: MINDEN páciens ({patients_count} db)\nTárgy: {subject}"
            else:
                selected_count = len(self.patients_tree.selection())
                confirm_msg = f"Biztos elküldi az üzenetet?\n\nCímzettek: Kijelölt páciensek ({selected_count} db)\nTárgy: {subject}"
            
            if messagebox.askyesno("Megerősítés", confirm_msg):
                sent_count = 0
                error_count = 0
                
                if recipients_mode == 'all':
                    # Minden páciensnek küldés
                    patients = self.db_manager.get_patients()
                    
                    for patient in patients:
                        try:
                            success, message = self.email_manager.send_email(
                                patient[2], subject, body, patient[1]
                            )
                            
                            if success:
                                sent_count += 1
                                self.db_manager.add_log("INFO", f"Azonnali üzenet elküldve: {patient[1]}", patient[2])
                            else:
                                error_count += 1
                                self.db_manager.add_log("ERROR", f"Üzenet hiba: {message}", patient[2])
                        
                        except Exception as e:
                            error_count += 1
                            self.db_manager.add_log("ERROR", f"Email küldési hiba {patient[1]}: {str(e)}")
                
                else:  # selected
                    # Kijelölt pácienseknek küldés
                    selections = self.patients_tree.selection()
                    
                    for selection in selections:
                        try:
                            item = self.patients_tree.item(selection)
                            patient_email = item['values'][1]
                            patient_name = item['text']
                            
                            success, message = self.email_manager.send_email(
                                patient_email, subject, body, patient_name
                            )
                            
                            if success:
                                sent_count += 1
                                self.db_manager.add_log("INFO", f"Azonnali üzenet elküldve: {patient_name}", patient_email)
                            else:
                                error_count += 1
                                self.db_manager.add_log("ERROR", f"Üzenet hiba: {message}", patient_email)
                        
                        except Exception as e:
                            error_count += 1
                            self.db_manager.add_log("ERROR", f"Email küldési hiba: {str(e)}")
                
                # Eredmény megjelenítése
                result_msg = f"Küldés befejezve!\n\nSikeres: {sent_count}\nHibák: {error_count}"
                if error_count > 0:
                    result_msg += "\n\nA hibákat a 'Naplók' fülön tekintheti meg."
                
                messagebox.showinfo("Befejezve", result_msg)
                
                # Mezők törlése siker esetén
                if sent_count > 0:
                    self.clear_message()
                
        except Exception as e:
            messagebox.showerror("Hiba", f"Üzenet küldési hiba: {str(e)}")
            self.db_manager.add_log("ERROR", f"Azonnali üzenet küldési hiba: {str(e)}")
    
    def preview_message(self):
        """Üzenet előnézete"""
        subject = self.message_subject.get().strip()
        body = self.message_body.get('1.0', tk.END).strip()
        
        if not subject or not body:
            messagebox.showerror("Hiba", "Tárgy és üzenet megadása kötelező!")
            return
        
        # Előnézet ablak
        preview_window = tk.Toplevel(self.root)
        preview_window.title("Üzenet előnézete")
        preview_window.geometry("600x500")
        preview_window.configure(bg=self.colors['bg_main'])
        
        ttk.Label(preview_window, text="Email előnézet", 
                 style='Title.TLabel').pack(pady=10)
        
        ttk.Label(preview_window, text=f"Tárgy: {subject}", 
                 style='Modern.TLabel').pack(anchor='w', padx=20, pady=5)
        
        ttk.Label(preview_window, text="Üzenet:", 
                 style='Modern.TLabel').pack(anchor='w', padx=20)
        
        preview_text = scrolledtext.ScrolledText(preview_window, height=20, width=70, 
                                               font=('Segoe UI', 10), state='disabled')
        preview_text.pack(padx=20, pady=10, fill='both', expand=True)
        
        # Minta páciens adatokkal
        sample_body = body.replace('{patient_name}', 'Minta Páciens')
        sample_body = sample_body.replace('{clinic_name}', self.clinic_name.get() or 'Orvosi Rendelő')
        sample_body = sample_body.replace('{appointment_date}', '2024-01-15')
        sample_body = sample_body.replace('{appointment_time}', '10:30')
        
        preview_text.config(state='normal')
        preview_text.insert('1.0', sample_body)
        preview_text.config(state='disabled')
        
        ttk.Button(preview_window, text="Bezárás", 
                  command=preview_window.destroy,
                  style='Secondary.TButton').pack(pady=10)
    
    def clear_message(self):
        """Üzenet mezők törlése"""
        self.message_subject.set("")
        self.message_body.delete('1.0', tk.END)
    
    def refresh_selected_patients(self):
        """Kijelölt páciensek frissítése az azonnali üzenetek fülön"""
        try:
            # Ellenőrzés, hogy létezik-e a listbox
            if not hasattr(self, 'selected_patients_listbox'):
                return
                
            # Listbox törlése
            self.selected_patients_listbox.delete(0, tk.END)
            
            # Ellenőrzés, hogy létezik-e a patients_tree
            if not hasattr(self, 'patients_tree'):
                self.selected_patients_listbox.insert(tk.END, "Páciensek lista még nem elérhető")
                return
            
            # Kijelölt páciensek lekérése a páciensek fülről
            selections = self.patients_tree.selection()
            
            if not selections:
                self.selected_patients_listbox.insert(tk.END, "Nincs kijelölt páciens! Menjen a 'Páciensek' fülre!")
                self.selected_patients_listbox.insert(tk.END, "Jelölje ki a kívánt pácienseket! Kattintson a 'Kijelölés frissítése' gombra!")
                return
            
            # Kijelölt páciensek megjelenítése
            self.selected_patients_listbox.insert(tk.END, f"{len(selections)} páciens kijelölve:")
            self.selected_patients_listbox.insert(tk.END, "")
            
            for selection in selections:
                item = self.patients_tree.item(selection)
                patient_name = item['text']
                patient_email = item['values'][1] if len(item['values']) > 1 else 'Nincs email'
                
                # Formázott megjelenítés
                display_text = f"{patient_name}"
                self.selected_patients_listbox.insert(tk.END, display_text)
                self.selected_patients_listbox.insert(tk.END, f"    {patient_email}")
                
        except Exception as e:
            if hasattr(self, 'selected_patients_listbox'):
                self.selected_patients_listbox.delete(0, tk.END)
                self.selected_patients_listbox.insert(tk.END, f"Hiba: {str(e)}")
                self.selected_patients_listbox.insert(tk.END, "Próbálja újra a frissítést!")

    # Template methods
    def load_appointment_change_template(self):
        """Időpont változás sablon betöltése"""
        self.message_subject.set("Időpont változás értesítés")
        template = """Kedves {patient_name}!

Tájékoztatjuk, hogy az Ön időpontja módosításra került.

Új időpont: {appointment_date} {appointment_time}

Kérjük, erősítse meg részvételét!

Üdvözlettel,
{clinic_name}"""
        
        self.message_body.delete('1.0', tk.END)
        self.message_body.insert('1.0', template)
    
    def load_clinic_info_template(self):
        """Rendelő információ sablon betöltése"""
        self.message_subject.set("Fontos információk a rendelőről")
        template = """Kedves {patient_name}!

Fontos információkat szeretnénk megosztani Önnel:

• Rendelési idő változás
• Új szolgáltatások
• Elérhetőségek frissítése

További információkért keressen minket!

Üdvözlettel,
{clinic_name}"""
        
        self.message_body.delete('1.0', tk.END)
        self.message_body.insert('1.0', template)
    
    def load_urgent_template(self):
        """Sürgős értesítés sablon betöltése"""
        self.message_subject.set("SÜRGŐS - Fontos értesítés")
        template = """Kedves {patient_name}!

SÜRGŐS ÉRTESÍTÉS:

[Itt adja meg a sürgős információt]

Kérjük, haladéktalanul vegye fel velünk a kapcsolatot!

Sürgős elérhetőség: [telefonszám]

Üdvözlettel,
{clinic_name}"""
        
        self.message_body.delete('1.0', tk.END)
        self.message_body.insert('1.0', template)
    
    def load_holiday_template(self):
        """Ünnepek sablon betöltése"""
        self.message_subject.set("Ünnepi köszöntő és rendelési rend")
        template = """Kedves {patient_name}!

Boldog Ünnepeket kívánunk!

Ünnepi rendelési rend:
• [Dátum]: Zárva
• [Dátum]: Rövidített nyitvatartás

Az ünnepek után [dátum]-től állunk ismét rendelkezésére.

Kellemes ünnepeket!

{clinic_name}"""
        
        self.message_body.delete('1.0', tk.END)
        self.message_body.insert('1.0', template)
    
    # Automation methods
    def start_automation(self):
        """Automatizálás indítása"""
        try:
            if self.automation_manager.running:
                messagebox.showinfo("Figyelem", "Az automatizálás már fut!")
                return
            
            self.automation_manager.start_automation()
            self.automation_status.set("Fut")
            messagebox.showinfo("Siker", "Automatizálás sikeresen elindítva!")
            
        except Exception as e:
            messagebox.showerror("Hiba", f"Automatizálás indítási hiba: {str(e)}")
    
    def stop_automation(self):
        """Automatizálás leállítása"""
        try:
            self.automation_manager.stop_automation()
            self.automation_status.set("Leállítva")
            messagebox.showinfo("Siker", "Automatizálás leállítva!")
            
        except Exception as e:
            messagebox.showerror("Hiba", f"Automatizálás leállítási hiba: {str(e)}")
    
    def send_immediate_reminders(self):
        """Azonnali emlékeztetők küldése"""
        try:
            reminders = self.db_manager.get_tomorrows_reminders()
            sent_count = 0
            
            for event in reminders:
                try:
                    patient_email = event[2]
                    if patient_email:
                        patient = self.db_manager.get_patient_by_email(patient_email)
                        
                        if patient:
                            start_time = datetime.strptime(event[5], '%Y-%m-%d %H:%M:%S')
                            appointment_date = start_time.strftime("%Y-%m-%d")
                            appointment_time = start_time.strftime("%H:%M")
                            
                            success, message = self.email_manager.send_appointment_reminder(
                                patient_email, patient[1], appointment_date, appointment_time
                            )
                            
                            if success:
                                self.db_manager.mark_reminder_sent(event[0])
                                self.db_manager.add_log("INFO", f"Azonnali emlékeztető elküldve: {patient[1]}", patient_email)
                                sent_count += 1
                            else:
                                self.db_manager.add_log("ERROR", f"Emlékeztető hiba: {message}", patient_email)
                
                except Exception as e:
                    print(f"Emlékeztető küldési hiba: {str(e)}")
                    continue
            
            messagebox.showinfo("Befejezve", f"{sent_count} azonnali emlékeztető elküldve!")
            
        except Exception as e:
            messagebox.showerror("Hiba", f"Azonnali emlékeztető hiba: {str(e)}")
    
    def send_new_appointment_notifications(self):
        """Mai új időpontok értesítése"""
        try:
            new_appointments = self.db_manager.get_todays_new_appointments()
            sent_count = 0
            
            for event in new_appointments:
                try:
                    patient_email = event[2]
                    if patient_email:
                        patient = self.db_manager.get_patient_by_email(patient_email)
                        
                        if patient:
                            start_time = datetime.strptime(event[5], '%Y-%m-%d %H:%M:%S')
                            appointment_date = start_time.strftime("%Y-%m-%d")
                            appointment_time = start_time.strftime("%H:%M")
                            
                            success, message = self.email_manager.send_new_appointment_notification(
                                patient_email, patient[1], appointment_date, appointment_time
                            )
                            
                            if success:
                                self.db_manager.mark_new_appointment_notified(event[0])
                                self.db_manager.add_log("INFO", f"Új időpont értesítés elküldve: {patient[1]}", patient_email)
                                sent_count += 1
                            else:
                                self.db_manager.add_log("ERROR", f"Új időpont értesítési hiba: {message}", patient_email)
                
                except Exception as e:
                    print(f"Új időpont értesítési hiba: {str(e)}")
                    continue
            
            messagebox.showinfo("Befejezve", f"{sent_count} új időpont értesítés elküldve!")
            
        except Exception as e:
            messagebox.showerror("Hiba", f"Új időpont értesítési hiba: {str(e)}")
    
    # Template management
    def load_template(self):
        """Email sablon betöltése"""
        try:
            # Alapértelmezett sablonok betöltése a változókból
            template_type = self.template_type.get()
            language = self.template_language.get()
            
            # Egyszerű sablon használata
            if language == 'de':
                if template_type == 'reminder':
                    subject = "Erinnerung - Ihr Termin morgen"
                    body = """Liebe/r {patient_name}!

Wir möchten Sie daran erinnern, dass Sie morgen einen Termin bei uns haben.

Bitte kommen Sie pünktlich!

Mit freundlichen Grüßen,
{clinic_name}"""
                else:
                    subject = "Terminbestätigung"
                    body = """Liebe/r {patient_name}!

Wir bestätigen Ihren Termin:
({appointment_date}) {appointment_time}

Mit freundlichen Grüßen,
{clinic_name}"""
            else:
                if template_type == 'reminder':
                    subject = "Emlékeztető - Időpontja holnap"
                    body = """Kedves {patient_name}!

Emlékeztetjük, hogy holnap időpontja van nálunk.

Kérjük, érkezzen pontosan!

Üdvözlettel,
{clinic_name}"""
                else:
                    subject = "Időpont megerősítése"
                    body = """Kedves {patient_name}!

Megerősítjük, hogy ({appointment_date}) {appointment_time}-kor időpontja van nálunk.

Üdvözlettel,
{clinic_name}"""
            
            self.template_subject.set(subject)
            self.template_body.delete('1.0', tk.END)
            self.template_body.insert('1.0', body)
            
        except Exception as e:
            messagebox.showerror("Hiba", f"Sablon betöltési hiba: {str(e)}")
    
    def save_template(self):
        """Email sablon mentése"""
        messagebox.showinfo("Információ", "Sablon mentés funkcionalitás fejlesztés alatt.")
    
    # Logs management
    def refresh_logs(self):
        """Naplók frissítése"""
        # Jelenlegi elemek törlése
        for item in self.logs_tree.get_children():
            self.logs_tree.delete(item)
        
        # Naplók betöltése
        logs = self.db_manager.get_logs(200)  # Utolsó 200 napló
        
        for log in logs:
            # Szín beállítása a log szint szerint
            tag = ''
            if log[1] == 'ERROR':
                tag = 'error'
            elif log[1] == 'WARNING':
                tag = 'warning'
            elif log[1] == 'INFO':
                tag = 'info'
            
            self.logs_tree.insert('', 'end', values=log, tags=(tag,))
        
        # Tag színek beállítása
        self.logs_tree.tag_configure('error', foreground='red')
        self.logs_tree.tag_configure('warning', foreground='orange')
        self.logs_tree.tag_configure('info', foreground='blue')
    
    def clear_logs(self):
        """Naplók törlése"""
        if messagebox.askyesno("Megerősítés", "Biztos törli az összes naplót?"):
            try:
                conn = sqlite3.connect(self.db_manager.db_name)
                cursor = conn.cursor()
                cursor.execute('DELETE FROM logs')
                conn.commit()
                conn.close()
                
                self.refresh_logs()
                messagebox.showinfo("Siker", "Naplók törölve!")
                
            except Exception as e:
                messagebox.showerror("Hiba", f"Napló törlési hiba: {str(e)}")
    
    def on_closing(self):
        """Alkalmazás bezárása"""
        if messagebox.askokcancel("Kilépés", "Biztos kilép az alkalmazásból?"):
            # Automatizálás leállítása
            if self.automation_manager.running:
                self.automation_manager.stop_automation()
            
            self.db_manager.add_log("INFO", "Alkalmazás bezárva")
            self.root.destroy()


def main():
    """Főfüggvény"""
    # Szükséges könyvtárak ellenőrzése
    required_packages = [
        'tkinter', 'sqlite3', 'json', 'smtplib', 'schedule', 
        'threading', 'pandas', 'cryptography'
    ]
    
    missing_packages = []
    
    try:
        import pandas
    except ImportError:
        missing_packages.append('pandas')
    
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        missing_packages.append('cryptography')
    
    try:
        import schedule
    except ImportError:
        missing_packages.append('schedule')
    
    if missing_packages:
        print("FIGYELEM: Hiányzó Python csomagok!")
        print("Telepítse a következő csomagokat:")
        for package in missing_packages:
            print(f"  pip install {package}")
        print("\nGoogle Calendar integrációhoz:")
        print("  pip install google-api-python-client google-auth-oauthlib")
        print()
    
    # GUI indítása
    root = tk.Tk()
    app = ModernPatientReminderApp(root)
    
    # Alkalmazás indítási napló
    app.db_manager.add_log("INFO", "Páciens Email Emlékeztető Rendszer v2.0 elindítva")
    
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\nAlkalmazás megszakítva...")
        if app.automation_manager.running:
            app.automation_manager.stop_automation()


if __name__ == "__main__":
    main()


# ===============================================
# TELEPÍTÉSI ÉS HASZNÁLATI ÚTMUTATÓ
# ===============================================

"""
TELEPÍTÉSI ÚTMUTATÓ:
===================

1. Python csomagok telepítése:
   pip install pandas cryptography schedule
   pip install google-api-python-client google-auth-oauthlib

2. Google Calendar API beállítása:
   - Menjen a Google Cloud Console-ra (https://console.cloud.google.com/)
   - Hozzon létre új projektet vagy válasszon meglévőt
   - Engedélyezze a Google Calendar API-t
   - Hozzon létre OAuth 2.0 credentials-t (Desktop Application)
   - Hozzon létre credentials.json fájlt az alkalmazás mappájába

3. Gmail App Password (alkalmazásjelszó) beállítása (ajánlott):
   - Gmail beállítások > Security > 2-Step Verification
   - App passwords > Generate new app password
   - Használja ezt a jelszót az alkalmazásban

MŰKÖDÉS:
=====================

1. EMLÉKEZTETŐK - Naponta egyszer 12:00-kor:
   - Holnapi időpontokra automatikus emlékeztetők
   - Csak olyan eseményekre, amikre még nem küldtünk emlékeztetőt
   - Automatikus páciens felismerés email alapján

2. ÚJ IDŐPONTOK - Naponta egyszer 15:30-kor:
   - Mai napon létrehozott új időpontok visszaigazolása
   - Csak olyan eseményekre, amikről még nem értesítettük a pácienst
   - Külön jelölés az új időpontokra



HASZNÁLATI ÚTMUTATÓ:
===================

1. Első indítás:
   - Állítsa be az email beállításokat a "Beállítások" fülön
   - Tesztelje az email küldést
   - Opcionálisan állítsa be a Google Calendar integrációt

2. Páciensek kezelése:
   - Adjon hozzá pácienseket egyenként vagy Excel importtal
   - Excel formátum: név, email, telefon (opcionális), nyelv (opcionális)

3. Google Calendar integráció:
   - Authentikáljon a Google fiókjával
   - A rendszer automatikusan szinkronizálja a következő 30 nap eseményeit
   - A "Naptár" fülön láthatja az összes időpontot és a pácienseket

4. Automatizálás:
   - Engedélyezze az automatikus emlékeztetőket
   - 12:00-kor: holnapi emlékeztetők
   - 15:30-kor: mai új időpontok értesítése

5. Azonnali üzenetek:
   - Az "Azonnali üzenetek" fülön tömegesen küldhet emaileket
   - Választhat minden páciens vagy csak kijelöltek között
   - Használhatja a beépített sablonokat

BIZTONSÁGI MEGJEGYZÉSEK:
========================

- A jelszavak titkosítva vannak tárolva
- Az alkalmazás GDPR kompatibilis adatkezelést használ
- Rendszeres biztonsági mentések ajánlottak az adatbázisról
- Ne ossza meg a credentials.json és config.json fájlokat

HIBAELHÁRÍTÁS:
==============

1. Email küldési hibák:
   - Ellenőrizze az SMTP beállításokat
   - Gmail esetén használjon App Password-ot
   - Ellenőrizze a tűzfal beállításokat

2. Google Calendar hibák:
   - Ellenőrizze a credentials.json fájlt
   - Újra authentikáljon szükség esetén
   - Ellenőrizze az internet kapcsolatot

3. Adatbázis hibák:
   - Ellenőrizze a fájl írási jogosultságokat
   - Készítsen biztonsági mentést rendszeresen

HASZNÁLATI TIPPEK:
==================

Excel import formátum:
   Név | Email | Telefon | Nyelv
   Kovács János | kovacs@email.com | +36301234567 | hu
   Tischler Maria | tischler@email.com | +43664123456 | de

Google Calendar esemény felismerés:
   - Az esemény leírásában vagy címében szerepeljen a páciens email címe
   - Használjon egyértelmű formátumot: pelda@email.com
"""