import os
import logging
import pandas as pd
from io import BytesIO
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import or_

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'amt_enterprise_ops_2026_premium'

# --- RENDER DISK PATHING ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if os.path.exists('/opt/render/project/src/uploads'):
    UPLOAD_FOLDER = '/opt/render/project/src/uploads'
else:
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
db_path = os.path.join(UPLOAD_FOLDER, 'amt_v5_pro.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(100))
    role = db.Column(db.String(20)) # 'admin' or 'viewer'

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
    class_society = db.Column(db.String(50)) # NEW: Class section
    vessel_type = db.Column(db.String(50))
    certificates = db.relationship('Certificate', backref='vessel', lazy=True, cascade="all, delete-orphan")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vessel_id = db.Column(db.Integer, db.ForeignKey('vessel.id'))
    name = db.Column(db.String(100))
    category = db.Column(db.String(50)) 
    expiry_date = db.Column(db.Date)
    file_path = db.Column(db.String(200), nullable=True) # OPTIONAL UPLOAD
    is_condition_of_class = db.Column(db.Boolean, default=False)

    def get_status(self):
        if not self.expiry_date: return {"color": "secondary", "label": "No Date", "val": 3}
        days = (self.expiry_date - date.today()).days
        if days <= 0: return {"color": "danger", "label": f"Overdue ({abs(days)}d)", "val": 0}
        if days <= 90: return {"color": "warning", "label": f"Expiring ({days}d)", "val": 1}
        return {"color": "success", "label": f"Valid ({days}d)", "val": 2}

# --- HELPERS ---
def log_action(action):
    log = AuditLog(user=current_user.username, action=action)
    db.session.add(log)
    db.session.commit()

# --- INITIALIZATION ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(username='admin', password='admin_password_2026', role='admin'))
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.password == request.form.get('password'):
            login_user(user)
            log_action("Logged in to the system")
            return redirect(url_for('dashboard'))
        flash("Invalid Credentials")
    return render_template('login.html')

@app.route('/')
@login_required
def dashboard():
    search = request.args.get('search', '')
    query = Vessel.query
    if search:
        query = query.filter(or_(Vessel.name.contains(search), Vessel.imo.contains(search)))
    vessels = query.all()
    
    # Alert Bell Logic: Get all overdue/expiring certs
    alerts = []
    for v in Vessel.query.all():
        for c in v.certificates:
            status = c.get_status()
            if status['color'] in ['danger', 'warning']:
                alerts.append({'vessel': v.name, 'cert': c.name, 'label': status['label'], 'color': status['color']})
                
    return render_template('dashboard.html', vessels=vessels, alerts=alerts)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role != 'admin': return redirect('/')
    vessels = Vessel.query.all()
    users = User.query.all()
    audit_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(50).all()

    if request.method == 'POST':
        # 1. Add Vessel
        if 'add_vessel' in request.form:
            v = Vessel(name=request.form['name'], imo=request.form['imo'], flag=request.form['flag'], class_society=request.form['class_society'], vessel_type=request.form['type'])
            db.session.add(v)
            log_action(f"Added new vessel: {v.name}")
        
        # 2. Add Certificate (File Optional)
        elif 'upload_cert' in request.form:
            f = request.files.get('file')
            fname = None
            if f and f.filename != '':
                fname = secure_filename(f"{request.form['vessel_id']}_{f.filename}")
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
            
            c = Certificate(
                vessel_id=request.form['vessel_id'], 
                name=request.form['cert_name'], 
                category=request.form['category'], 
                expiry_date=datetime.strptime(request.form['expiry'], '%Y-%m-%d').date(), 
                file_path=fname, 
                is_condition_of_class=('is_coc' in request.form)
            )
            db.session.add(c)
            log_action(f"Added certificate {c.name} for Vessel ID {c.vessel_id}")

        # 3. Create User
        elif 'add_user' in request.form:
            u = User(username=request.form['new_user'], password=request.form['new_pass'], role=request.form['new_role'])
            db.session.add(u)
            log_action(f"Created user: {u.username} with role {u.role}")

        db.session.commit()
        return redirect(url_for('admin'))
    
    return render_template('admin.html', vessels=vessels, users=users, audit_logs=audit_logs)

@app.route('/export_excel')
@login_required
def export_excel():
    vessels = Vessel.query.all()
    data = []
    for v in vessels:
        for c in v.certificates:
            data.append({
                "Vessel Name": v.name,
                "IMO": v.imo,
                "Flag": v.flag,
                "Class": v.class_society,
                "Certificate": c.name,
                "Expiry Date": c.expiry_date,
                "Status": c.get_status()['label']
            })
    
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Fleet Status')
    output.seek(0)
    
    log_action("Exported Fleet Status to Excel")
    return send_file(output, download_name=f"AMT_Fleet_Status_{date.today()}.xlsx", as_attachment=True)

@app.route('/logout')
def logout():
    log_action("Logged out")
    logout_user(); return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
