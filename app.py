import os
import logging
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
app.config['SECRET_KEY'] = 'amt_premium_ops_2026'

# 2. Path Management (Bulletproof)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

# 3. Create Folder if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        logger.info(f"Successfully created: {UPLOAD_FOLDER}")
    except Exception as e:
        # Fallback to a local path if the absolute path fails
        UPLOAD_FOLDER = 'uploads'
        if not os.path.exists(UPLOAD_FOLDER):
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        logger.error(f"Manual folder creation error: {e}. Using fallback: {UPLOAD_FOLDER}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 4. Database Config
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'amt_ops_vfinal.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(100))
    role = db.Column(db.String(20))

class Vessel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    imo = db.Column(db.String(20), unique=True)
    flag = db.Column(db.String(50))
    vessel_type = db.Column(db.String(50))
    certificates = db.relationship('Certificate', backref='vessel', lazy=True, cascade="all, delete-orphan")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vessel_id = db.Column(db.Integer, db.ForeignKey('vessel.id'))
    name = db.Column(db.String(100))
    category = db.Column(db.String(50)) 
    expiry_date = db.Column(db.Date)
    file_path = db.Column(db.String(200))
    is_condition_of_class = db.Column(db.Boolean, default=False)

    def get_status(self):
        if not self.expiry_date: return {"color": "secondary", "label": "No Date"}
        days = (self.expiry_date - date.today()).days
        if days <= 0: return {"color": "danger", "label": f"Overdue ({abs(days)}d)"}
        if days <= 90: return {"color": "warning", "label": f"Expiring ({days}d)"}
        return {"color": "success", "label": f"Valid ({days}d)"}

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
            return redirect(url_for('dashboard'))
        flash("Incorrect username or password")
    return render_template('login.html')

@app.route('/')
@login_required
def dashboard():
    search = request.args.get('search', '')
    query = Vessel.query
    if search:
        query = query.filter(or_(Vessel.name.contains(search), Vessel.imo.contains(search)))
    vessels = query.all()
    
    total_certs = Certificate.query.count()
    green_certs = sum(1 for c in Certificate.query.all() if c.get_status()['color'] == 'success')
    health = round((green_certs / total_certs * 100)) if total_certs > 0 else 100
    
    return render_template('dashboard.html', vessels=vessels, health=health)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))
    vessels = Vessel.query.all()
    if request.method == 'POST':
        if 'add_vessel' in request.form:
            v = Vessel(name=request.form['name'], imo=request.form['imo'], flag=request.form['flag'], vessel_type=request.form['type'])
            db.session.add(v)
        elif 'upload_cert' in request.form:
            f = request.files.get('file')
            if f and f.filename != '':
                filename = secure_filename(f"{request.form['vessel_id']}_{f.filename}")
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                f.save(save_path)
                c = Certificate(
                    vessel_id=request.form['vessel_id'], 
                    name=request.form['cert_name'], 
                    category=request.form['category'], 
                    expiry_date=datetime.strptime(request.form['expiry'], '%Y-%m-%d').date(), 
                    file_path=filename, 
                    is_condition_of_class=('is_coc' in request.form)
                )
                db.session.add(c)
        db.session.commit()
        return redirect(url_for('admin'))
    return render_template('admin.html', vessels=vessels)

@app.route('/uploads/<filename>')
@login_required
def view_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/backup')
@login_required
def backup():
    db_path = os.path.join(BASE_DIR, 'amt_ops_vfinal.db')
    if os.path.exists(db_path):
        return send_file(db_path, as_attachment=True)
    return "Database file not found", 404

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin', password='admin_password_2026', role='admin'))
            db.session.commit()
    app.run(debug=True)
