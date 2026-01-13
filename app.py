import os
import logging
import pandas as pd
from io import BytesIO
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import or_

# 1. Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'amt_master_enterprise_2026'

# 2. Render Disk Pathing
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if os.path.exists('/opt/render/project/src/uploads'):
    UPLOAD_FOLDER = '/opt/render/project/src/uploads'
else:
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Database sits on the Persistent Disk
db_path = os.path.join(UPLOAD_FOLDER, 'amt_v7_final.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# 3. Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(100))
    role = db.Column(db.String(20))

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(50))
    action = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Vessel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    imo = db.Column(db.String(20), unique=True)
    flag = db.Column(db.String(50))
    class_society = db.Column(db.String(50))
    certificates = db.relationship('Certificate', backref='vessel', lazy=True, cascade="all, delete-orphan")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vessel_id = db.Column(db.Integer, db.ForeignKey('vessel.id'))
    name = db.Column(db.String(100))
    category = db.Column(db.String(50)) 
    expiry_date = db.Column(db.Date)
    file_path = db.Column(db.String(200), nullable=True)

    def get_status(self):
        if not self.expiry_date: return {"bg": "secondary", "label": "No Date"}
        days = (self.expiry_date - date.today()).days
        if days <= 0: return {"bg": "danger", "label": f"EXPIRED"}
        if days <= 90: return {"bg": "warning", "label": f"{days} Days"}
        return {"bg": "success", "label": "VALID"}

def log_action(msg):
    log = AuditLog(user=current_user.username if current_user.is_authenticated else "System", action=msg)
    db.session.add(log)
    db.session.commit()

# 4. Initialization
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(username='admin', password='admin_password_2026', role='admin'))
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 5. Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.password == request.form.get('password'):
            login_user(user)
            log_action("User Login")
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/')
@login_required
def dashboard():
    vessels = Vessel.query.all()
    alerts = []
    for v in vessels:
        for c in v.certificates:
            if c.get_status()['bg'] in ['danger', 'warning']:
                alerts.append({'v': v.name, 'c': c.name, 's': c.get_status()['label'], 'bg': c.get_status()['bg']})
    return render_template('dashboard.html', vessels=vessels, alerts=alerts)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        action = request.form.get('form_action')
        if action == 'add_vessel':
            v = Vessel(name=request.form.get('name'), imo=request.form.get('imo'), flag=request.form.get('flag'), class_society=request.form.get('class_society'))
            db.session.add(v)
            log_action(f"Added Vessel {v.name}")
        elif action == 'add_cert':
            f = request.files.get('file')
            fname = secure_filename(f.filename) if f and f.filename != '' else None
            if fname: f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
            c = Certificate(vessel_id=request.form.get('vessel_id'), name=request.form.get('cert_name'), category=request.form.get('category'), expiry_date=datetime.strptime(request.form.get('expiry'), '%Y-%m-%d').date(), file_path=fname)
            db.session.add(c)
            log_action(f"Added Cert {c.name}")
        elif action == 'add_user':
            u = User(username=request.form.get('new_user'), password=request.form.get('new_pass'), role=request.form.get('new_role'))
            db.session.add(u)
        elif action == 'restore_db':
            f = request.files.get('backup_file')
            if f:
                f.save(db_path)
                return redirect(url_for('logout'))
        db.session.commit()
    return render_template('admin.html', vessels=Vessel.query.all(), users=User.query.all(), logs=AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(15).all())

@app.route('/cert/<int:id>/delete')
@login_required
def delete_cert(id):
    c = Certificate.query.get_or_404(id)
    log_action(f"Deleted Cert {c.name}")
    db.session.delete(c)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/cert/<int:id>/update', methods=['POST'])
@login_required
def update_cert(id):
    c = Certificate.query.get_or_404(id)
    c.name = request.form.get('new_name')
    c.expiry_date = datetime.strptime(request.form.get('new_expiry'), '%Y-%m-%d').date()
    log_action(f"Updated Cert {c.name}")
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/backup')
@login_required
def backup():
    log_action("Downloaded DB Backup")
    return send_file(db_path, as_attachment=True)

@app.route('/uploads/<filename>')
@login_required
def view_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
