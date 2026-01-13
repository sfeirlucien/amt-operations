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

# 1. Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'amt_enterprise_2026_pro'

# 2. Pathing Logic
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if os.path.exists('/opt/render/project/src/uploads'):
    UPLOAD_FOLDER = '/opt/render/project/src/uploads'
else:
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
db_path = os.path.join(UPLOAD_FOLDER, 'amt_v6_pro.db')
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
    vessel_type = db.Column(db.String(50))
    certificates = db.relationship('Certificate', backref='vessel', lazy=True, cascade="all, delete-orphan")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vessel_id = db.Column(db.Integer, db.ForeignKey('vessel.id'))
    name = db.Column(db.String(100))
    category = db.Column(db.String(50)) 
    expiry_date = db.Column(db.Date)
    file_path = db.Column(db.String(200), nullable=True) 
    is_condition_of_class = db.Column(db.Boolean, default=False)

    def get_status(self):
        if not self.expiry_date: return {"color": "secondary", "label": "No Date", "bg": "secondary"}
        days = (self.expiry_date - date.today()).days
        if days <= 0: return {"color": "danger", "label": f"Overdue ({abs(days)}d)", "bg": "danger"}
        if days <= 90: return {"color": "warning", "label": f"Expiring ({days}d)", "bg": "warning"}
        return {"color": "success", "label": f"Valid ({days}d)", "bg": "success"}

# 4. Global Action Logger
def log_action(action):
    user_name = current_user.username if current_user.is_authenticated else "System"
    log = AuditLog(user=user_name, action=action)
    db.session.add(log)
    db.session.commit()

# 5. Initialization
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(username='admin', password='admin_password_2026', role='admin'))
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 6. Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.password == request.form.get('password'):
            login_user(user)
            log_action("Successful Login")
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
    
    alerts = []
    for v in Vessel.query.all():
        for c in v.certificates:
            s = c.get_status()
            if s['bg'] in ['danger', 'warning']:
                alerts.append({'vessel': v.name, 'cert': c.name, 'label': s['label'], 'bg': s['bg']})
    return render_template('dashboard.html', vessels=vessels, alerts=alerts)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        action_type = request.form.get('form_action')
        
        if action_type == 'add_vessel':
            v = Vessel(
                name=request.form.get('name'), 
                imo=request.form.get('imo'), 
                flag=request.form.get('flag'), 
                class_society=request.form.get('class_society'), 
                vessel_type=request.form.get('type')
            )
            db.session.add(v)
            log_action(f"Vessel Registered: {v.name}")
        
        elif action_type == 'upload_cert':
            f = request.files.get('file')
            fname = None
            v_id = request.form.get('vessel_id')
            if f and f.filename != '':
                fname = secure_filename(f"{v_id}_{f.filename}")
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
            
            exp_str = request.form.get('expiry')
            c = Certificate(
                vessel_id=v_id, 
                name=request.form.get('cert_name'), 
                category=request.form.get('category'), 
                expiry_date=datetime.strptime(exp_str, '%Y-%m-%d').date() if exp_str else None, 
                file_path=fname, 
                is_condition_of_class=('is_coc' in request.form)
            )
            db.session.add(c)
            log_action(f"Certificate Created: {c.name}")

        elif action_type == 'add_user':
            u = User(
                username=request.form.get('new_user'), 
                password=request.form.get('new_pass'), 
                role=request.form.get('new_role')
            )
            db.session.add(u)
            log_action(f"Access Granted to User: {u.username}")

        db.session.commit()
        return redirect(url_for('admin'))
    
    vessels = Vessel.query.all()
    users = User.query.all()
    audit_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(20).all()
    return render_template('admin.html', vessels=vessels, users=users, audit_logs=audit_logs)

@app.route('/export_excel')
@login_required
def export_excel():
    data = []
    for v in Vessel.query.all():
        for c in v.certificates:
            data.append({
                "Vessel": v.name, "IMO": v.imo, "Class": v.class_society, 
                "Cert": c.name, "Expiry": c.expiry_date, "Status": c.get_status()['label']
            })
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    log_action("Fleet Report Exported to Excel")
    return send_file(output, download_name=f"Fleet_Status_{date.today()}.xlsx", as_attachment=True)

@app.route('/uploads/<filename>')
@login_required
def view_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/logout')
def logout():
    log_action("User Logout")
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
