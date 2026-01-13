import os
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import or_

app = Flask(__name__)
app.config['SECRET_KEY'] = 'amt_premium_ops_2026'
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///amt_ops_v4.db'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

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
        if not self.expiry_date: return {"color": "secondary", "label": "No Date", "days": 0}
        days = (self.expiry_date - date.today()).days
        if days <= 0: return {"color": "danger", "label": f"Overdue ({abs(days)}d)", "days": days}
        if days <= 90: return {"color": "warning", "label": f"Expiring ({days}d)", "days": days}
        return {"color": "success", "label": f"Valid ({days}d)", "days": days}

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and user.password == request.form['password']:
            login_user(user)
            return redirect(url_for('dashboard'))
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
    if current_user.role != 'admin': return redirect('/')
    if request.method == 'POST':
        if 'add_vessel' in request.form:
            v = Vessel(name=request.form['name'], imo=request.form['imo'], flag=request.form['flag'], vessel_type=request.form['type'])
            db.session.add(v)
        elif 'upload_cert' in request.form:
            f = request.files['file']
            if f:
                filename = secure_filename(f"{request.form['vessel_id']}_{f.filename}")
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                c = Certificate(vessel_id=request.form['vessel_id'], name=request.form['cert_name'], category=request.form['category'], expiry_date=datetime.strptime(request.form['expiry'], '%Y-%m-%d').date(), file_path=filename, is_condition_of_class=('is_coc' in request.form))
                db.session.add(c)
        db.session.commit()
        return redirect(url_for('admin'))
    vessels = Vessel.query.all()
    return render_template('admin.html', vessels=vessels)

@app.route('/uploads/<filename>')
@login_required
def view_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/backup')
@login_required
def backup():
    return send_file('amt_ops_v4.db', as_attachment=True)

@app.route('/logout')
def logout():
    logout_user(); return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin', password='admin_password_2026', role='admin'))
            db.session.commit()
    app.run(host='0.0.0.0', port=5000)
