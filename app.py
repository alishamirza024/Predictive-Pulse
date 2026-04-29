from flask import Flask, render_template, request, flash, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin,
                         login_user, login_required,
                         logout_user, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import joblib, numpy as np, os, io, json
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib import colors as rl_colors
from reportlab.lib.utils import simpleSplit

# ─────────────────────────────────────────────
#  App & DB setup
# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')

app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{os.getenv('MYSQLUSER', 'root')}:{os.getenv('MYSQLPASSWORD', 'Root%40123')}"
    f"@{os.getenv('MYSQLHOST', 'localhost')}:{os.getenv('MYSQLPORT', '3306')}/{os.getenv('MYSQL_DATABASE', 'hypertension_db')}"
)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access the assessment platform.'
login_manager.login_message_category = 'info'

# ─────────────────────────────────────────────
#  Database Models
# ─────────────────────────────────────────────
doctor_patient = db.Table('doctor_patient',
    db.Column('doctor_id', db.Integer, db.ForeignKey('users.id')),
    db.Column('patient_id', db.Integer, db.ForeignKey('users.id'))
)

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20),  default='patient', nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    predictions   = db.relationship('PredictionRecord', backref='user', lazy=True)
    
    patients = db.relationship('User', 
                               secondary=doctor_patient,
                               primaryjoin=(id == doctor_patient.c.doctor_id),
                               secondaryjoin=(id == doctor_patient.c.patient_id),
                               backref=db.backref('doctors', lazy='dynamic'),
                               lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class PredictionRecord(db.Model):
    __tablename__ = 'predictions'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    result     = db.Column(db.String(50),  nullable=False)
    confidence = db.Column(db.Float,       nullable=False)
    priority   = db.Column(db.String(30),  nullable=False)
    input_data = db.Column(db.Text,        nullable=True)   # JSON-encoded form inputs
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)


class LinkRequest(db.Model):
    __tablename__ = 'link_requests'
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    patient = db.relationship('User', foreign_keys=[patient_id])


class DoctorProfile(db.Model):
    __tablename__ = 'doctor_profiles'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    specialization = db.Column(db.String(100), default='General Physician')
    experience_years = db.Column(db.Integer, default=0)
    availability_status = db.Column(db.String(20), default='Available') # Available, Busy, Offline

    user = db.relationship('User', backref=db.backref('doctor_profile', uselist=False))


class ConsultationRequest(db.Model):
    __tablename__ = 'consultation_requests'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    request_message = db.Column(db.Text, nullable=False)
    additional_notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='Pending') # Pending, Accepted, Rejected, Completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('User', foreign_keys=[patient_id], backref='sent_consultations')
    doctor = db.relationship('User', foreign_keys=[doctor_id], backref='received_consultations')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─────────────────────────────────────────────
#  Domain Data
# ─────────────────────────────────────────────
stage_map = {
    0: 'NORMAL',
    1: 'HYPERTENSION (Stage-1)',
    2: 'HYPERTENSION (Stage-2)',
    3: 'HYPERTENSIVE CRISIS'
}

color_map = {
    0: '☐ #10B981',
    1: '☐ #F59E0B',
    2: '☐ #F97316',
    3: '☐ #EF4444'
}

recommendations = {
    0: {
        'title': 'Normal Blood Pressure',
        'description': 'Your cardiovascular risk assessment indicates normal blood pressure levels.',
        'actions': [
            'Maintain current healthy lifestyle',
            'Regular physical activity (150 minutes/week)',
            'Continue balanced, low-sodium diet',
            'Annual blood pressure monitoring',
            'Regular health check-ups'
        ],
        'priority': 'Low Risk'
    },
    1: {
        'title': 'Stage 1 Hypertension',
        'description': 'Mild elevation detected requiring lifestyle modifications and medical consultation.',
        'actions': [
            'Schedule appointment with healthcare provider',
            'Implement DASH diet plan',
            'Increase physical activity gradually',
            'Monitor blood pressure bi-weekly',
            'Reduce sodium intake (<2300mg/day)',
            'Consider stress management techniques'
        ],
        'priority': 'Moderate Risk'
    },
    2: {
        'title': 'Stage 2 Hypertension',
        'description': 'Significant hypertension requiring immediate medical intervention and treatment.',
        'actions': [
            'URGENT: Consult physician within 1-2 days',
            'Likely medication therapy required',
            'Comprehensive cardiovascular assessment',
            'Daily blood pressure monitoring',
            'Strict dietary sodium restriction',
            'Lifestyle modification counseling'
        ],
        'priority': 'High Risk'
    },
    3: {
        'title': 'Hypertensive Crisis',
        'description': 'CRITICAL: Dangerously elevated blood pressure requiring emergency medical care.',
        'actions': [
            'EMERGENCY: Seek immediate medical attention',
            'Call 911 if experiencing symptoms',
            'Do not delay treatment',
            'Monitor for stroke/heart attack signs',
            'Prepare current medication list',
            'Avoid physical exertion'
        ],
        'priority': 'EMERGENCY'
    }
}

# ─────────────────────────────────────────────
#  Load Model
# ─────────────────────────────────────────────
try:
    model = joblib.load("logreg_model.pkl")
except FileNotFoundError:
    print("Warning: Model file not found. Using dummy predictions.")
    model = None

# ─────────────────────────────────────────────
#  Auth Routes
# ─────────────────────────────────────────────


@app.route('/login', methods=['GET', 'POST'])
def login():
    email = ""

    # If user is already logged in → pre-fill email
    if current_user.is_authenticated:
        email = current_user.email

    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        if not email or not password:
            flash('Please fill in all fields.', 'error')
            return render_template('login.html', email=email)

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            flash(f'Welcome back, {user.username}!', 'success')
            if user.role == 'doctor':
                return redirect(next_page or url_for('doctor_dashboard'))
            return redirect(next_page or url_for('home'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html', email=email)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username  = request.form.get('username', '').strip()
        email     = request.form.get('email', '').strip()
        password  = request.form.get('password', '')
        confirm   = request.form.get('confirm_password', '')
        role      = request.form.get('role', 'patient')
        # Doctor-only fields
        specialization   = request.form.get('specialization', 'General Physician').strip()
        experience_years = request.form.get('experience_years', '0').strip()

        # Validation
        if not all([username, email, password, confirm]):
            flash('Please fill in all fields.', 'error')
            return render_template('register.html')
        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'error')
            return render_template('register.html')
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('register.html')
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')
        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'error')
            return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'error')
            return render_template('register.html')
        if role == 'doctor':
            if not specialization:
                flash('Please enter your specialization.', 'error')
                return render_template('register.html')
            try:
                exp = int(experience_years)
                if exp < 0:
                    raise ValueError
            except ValueError:
                flash('Experience must be a valid non-negative number.', 'error')
                return render_template('register.html')

        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()  # get user.id before commit

        if role == 'doctor':
            profile = DoctorProfile(
                user_id=user.id,
                specialization=specialization,
                experience_years=int(experience_years),
                availability_status='Available'
            )
            db.session.add(profile)

        db.session.commit()

        login_user(user)
        flash(f'Account created! Welcome, {username}.', 'success')
        if user.role == 'doctor':
            return redirect(url_for('doctor_dashboard'))
        return redirect(url_for('home'))

    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'doctor':
        return redirect(url_for('doctor_dashboard'))

    pending_requests = LinkRequest.query.filter_by(patient_id=current_user.id, status='pending').all()

    from collections import defaultdict

    records_asc = (PredictionRecord.query
                   .filter_by(user_id=current_user.id)
                   .order_by(PredictionRecord.created_at.asc())
                   .all())

    total = len(records_asc)
    last  = records_asc[-1] if records_asc else None

    # ── Monthly chart (last 6 months) ─────────────
    priority_map = {'Low Risk': 1, 'Moderate Risk': 2, 'High Risk': 3, 'EMERGENCY': 4}
    plot_records = records_asc[-20:] if len(records_asc) > 20 else records_asc
    chart_labels = [r.created_at.strftime('%d %b') for r in plot_records]
    chart_data = [priority_map.get(r.priority, 1) for r in plot_records]
    chart_colors = ['#EF4444' if r.priority == 'EMERGENCY' else '#0891B2' for r in plot_records]

    # ── Severity score map ────────────────────────
    severity_score = {
        'NORMAL': 0,
        'HYPERTENSION (Stage-1)': 1,
        'HYPERTENSION (Stage-2)': 2,
        'HYPERTENSIVE CRISIS': 3
    }

    # ── Trend indicator (last 3 records) ──────────
    trend_label = None
    trend_type  = None        # 'up' | 'down' | 'stable'
    if total >= 2:
        first_val = priority_map.get(records_asc[0].priority, 1)
        latest_val = priority_map.get(records_asc[-1].priority, 1)
        if latest_val > first_val:
            trend_label = 'Worsening ↓'
            trend_type  = 'up'
        elif latest_val < first_val:
            trend_label = 'Improving ↑'
            trend_type  = 'down'
        else:
            trend_label = 'Stable →'
            trend_type  = 'stable'

    # ── Health Insight Box ────────────────────────
    insights = []
    if total >= 3:
        last3_results = [r.result for r in records_asc[-3:]]
        last3_scores  = [severity_score.get(r, 0) for r in last3_results]

        # Consistent stage
        if len(set(last3_results)) == 1:
            stage_short = {
                'NORMAL': 'Normal', 'HYPERTENSION (Stage-1)': 'Stage-1',
                'HYPERTENSION (Stage-2)': 'Stage-2', 'HYPERTENSIVE CRISIS': 'Hypertensive Crisis'
            }.get(last3_results[0], last3_results[0])
            insights.append(f'You are consistently in {stage_short} range.')

        # Increasing trend
        if last3_scores[2] > last3_scores[0]:
            insights.append('Your last 3 readings show an increasing BP trend.')
            if last3_scores[2] >= 2:
                insights.append('Consider lifestyle changes and consult your doctor soon.')

        # Improving trend
        if last3_scores[2] < last3_scores[0]:
            insights.append('Your health is showing signs of improvement — keep it up!')

        # Crisis alert
        if any('CRISIS' in r for r in last3_results):
            insights.append('A recent reading indicates a hypertensive crisis — seek medical attention.')

    if total >= 1 and not insights:
        insights.append('Keep logging assessments to unlock personalised health insights.')

    return render_template(
        'dashboard.html',
        records      = list(reversed(records_asc)),   # newest first for table
        total        = total,
        last         = last,
        chart_labels = chart_labels,
        chart_data = chart_data,
        chart_colors = chart_colors,
        trend_label  = trend_label,
        trend_type   = trend_type,
        insights     = insights,
        pending_requests = pending_requests
    )


# ─────────────────────────────────────────────
#  Doctor Routes
# ─────────────────────────────────────────────
@app.route('/doctor_dashboard')
@login_required
def doctor_dashboard():
    if current_user.role != 'doctor':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))
    patients = current_user.patients.all()
    return render_template('doctor_dashboard.html', patients=patients)

@app.route('/doctor/request_link', methods=['POST'])
@login_required
def doctor_request_link():
    if current_user.role != 'doctor':
        return redirect(url_for('dashboard'))
    email = request.form.get('email', '').strip()
    patient = User.query.filter_by(email=email, role='patient').first()
    if not patient:
        flash('Patient not found.', 'error')
        return redirect(url_for('doctor_dashboard'))
    
    # Check if already linked
    if patient in current_user.patients:
        flash('Already linked to this patient.', 'info')
        return redirect(url_for('doctor_dashboard'))
        
    # Check if pending request exists
    existing = LinkRequest.query.filter_by(doctor_id=current_user.id, patient_id=patient.id, status='pending').first()
    if existing:
        flash('Link request already pending.', 'info')
        return redirect(url_for('doctor_dashboard'))
        
    req = LinkRequest(doctor_id=current_user.id, patient_id=patient.id)
    db.session.add(req)
    db.session.commit()
    flash('Link request sent successfully.', 'success')
    return redirect(url_for('doctor_dashboard'))

@app.route('/patient/handle_request/<int:request_id>/<action>', methods=['POST'])
@login_required
def patient_handle_request(request_id, action):
    req = LinkRequest.query.get_or_404(request_id)
    if req.patient_id != current_user.id or req.status != 'pending':
        flash('Invalid request.', 'error')
        return redirect(url_for('dashboard'))
        
    if action == 'approve':
        req.status = 'approved'
        doctor = User.query.get(req.doctor_id)
        doctor.patients.append(current_user)
        flash('Doctor link approved.', 'success')
    else:
        req.status = 'rejected'
        flash('Doctor link rejected.', 'info')
        
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/doctor/patient/<int:patient_id>')
@login_required
def doctor_patient_view(patient_id):
    if current_user.role != 'doctor':
        return redirect(url_for('dashboard'))
        
    patient = User.query.get_or_404(patient_id)
    if patient not in current_user.patients:
        flash('You do not have permission to view this patient.', 'error')
        return redirect(url_for('doctor_dashboard'))
        
    # Reuse dashboard logic
    from collections import defaultdict
    records_asc = PredictionRecord.query.filter_by(user_id=patient.id).order_by(PredictionRecord.created_at.asc()).all()
    
    total = len(records_asc)
    last = records_asc[-1] if records_asc else None
    
    priority_map = {'Low Risk': 1, 'Moderate Risk': 2, 'High Risk': 3, 'EMERGENCY': 4}
    plot_records = records_asc[-20:] if len(records_asc) > 20 else records_asc
    chart_labels = [r.created_at.strftime('%d %b') for r in plot_records]
    chart_data = [priority_map.get(r.priority, 1) for r in plot_records]
    chart_colors = ['#EF4444' if r.priority == 'EMERGENCY' else '#0891B2' for r in plot_records]
    
    severity_score = {'NORMAL': 0, 'HYPERTENSION (Stage-1)': 1, 'HYPERTENSION (Stage-2)': 2, 'HYPERTENSIVE CRISIS': 3}
    
    trend_label = None
    trend_type = None
    if total >= 2:
        first_val = priority_map.get(records_asc[0].priority, 1)
        latest_val = priority_map.get(records_asc[-1].priority, 1)
        if latest_val > first_val:
            trend_label = 'Worsening ↓'
            trend_type = 'up'
        elif latest_val < first_val:
            trend_label = 'Improving ↑'
            trend_type = 'down'
        else:
            trend_label = 'Stable →'
            trend_type = 'stable'
            
    insights = []
    if total >= 3:
        last3_results = [r.result for r in records_asc[-3:]]
        last3_scores = [severity_score.get(r, 0) for r in last3_results]
        if len(set(last3_results)) == 1:
            stage_short = {'NORMAL': 'Normal', 'HYPERTENSION (Stage-1)': 'Stage-1', 'HYPERTENSION (Stage-2)': 'Stage-2', 'HYPERTENSIVE CRISIS': 'Hypertensive Crisis'}.get(last3_results[0], last3_results[0])
            insights.append(f'Consistently in {stage_short} range.')
        if last3_scores[2] > last3_scores[0]:
            insights.append('Last 3 readings show an increasing BP trend.')
        if last3_scores[2] < last3_scores[0]:
            insights.append('Showing signs of improvement.')
        if any('CRISIS' in r for r in last3_results):
            insights.append('Recent reading indicates a hypertensive crisis.')
            
    if total >= 1 and not insights:
        insights.append('Insufficient data for advanced insights.')
        
    return render_template(
        'dashboard.html',
        records = list(reversed(records_asc)),
        total = total,
        last = last,
        chart_labels = chart_labels,
        chart_data = chart_data,
        chart_colors = chart_colors,
        trend_label = trend_label,
        trend_type = trend_type,
        insights = insights,
        doctor_view = True,
        patient = patient
    )

# ─────────────────────────────────────────────
#  Main Routes
# ─────────────────────────────────────────────
@app.route('/')
@login_required
def home():
    return render_template('index.html')


@app.route('/predict', methods=['POST'])
@login_required
def predict():
    try:
        required_fields = [
            'Gender', 'Age', 'History', 'Patient', 'TakeMedication',
            'Severity', 'BreathShortness', 'VisualChanges', 'NoseBleeding',
            'Whendiagnoused', 'Systolic', 'Diastolic', 'ControlledDiet'
        ]
        form_data = {}
        for field in required_fields:
            value = request.form.get(field)
            if not value:
                flash(f'Please complete all required fields: {field}', 'error')
                return render_template('index.html')
            form_data[field] = value

        try:
            encoded = [
                0 if form_data['Gender'] == 'Male' else 1,
                {'18-34': 1, '35-50': 2, '51-64': 3, '65+': 4}[form_data['Age']],
                1 if form_data['History'] == 'Yes' else 0,
                1 if form_data['Patient'] == 'Yes' else 0,
                1 if form_data['TakeMedication'] == 'Yes' else 0,
                {'Mild': 0, 'Moderate': 1, 'Severe': 2}[form_data['Severity']],
                1 if form_data['BreathShortness'] == 'Yes' else 0,
                1 if form_data['VisualChanges'] == 'Yes' else 0,
                1 if form_data['NoseBleeding'] == 'Yes' else 0,
                {'<1 Year': 1, '1 - 5 Years': 2, '>5 Years': 3}[form_data['Whendiagnoused']],
                {'100 - 110': 0, '111 - 120': 1, '121 - 130': 2, '130+': 3}[form_data['Systolic']],
                {'70 - 80': 0, '81 - 90': 1, '91 - 100': 2, '100+': 3}[form_data['Diastolic']],
                1 if form_data['ControlledDiet'] == 'Yes' else 0
            ]
        except KeyError as e:
            flash(f'Invalid selection: {e}', 'error')
            return render_template('index.html')

        scaled = encoded.copy()
        scaled[1]  = (encoded[1] - 1) / 3
        scaled[5]  = encoded[5] / 2
        scaled[9]  = (encoded[9] - 1) / 2
        scaled[10] = encoded[10] / 3
        scaled[11] = encoded[11] / 3

        input_array = np.array(scaled).reshape(1, -1)

        if model is not None:
            prediction = model.predict(input_array)[0]
            try:
                confidence = max(model.predict_proba(input_array)[0]) * 100
            except Exception:
                confidence = 85.0
        else:
            import random
            prediction = random.randint(0, 3)
            confidence = 87.5
            flash('Demo Mode: Using simulated prediction.', 'info')

        rec = recommendations[prediction]

        # Save to DB — include raw form inputs as JSON for PDF export
        record = PredictionRecord(
            user_id    = current_user.id,
            result     = stage_map[prediction],
            confidence = round(confidence, 2),
            priority   = rec['priority'],
            input_data = json.dumps(form_data)
        )
        db.session.add(record)
        db.session.commit()

        return render_template(
            'index.html',
            prediction_text     = stage_map[prediction],
            result_color        = color_map[prediction],
            confidence          = confidence,
            recommendation      = rec,
            form_data           = form_data
        )

    except Exception as e:
        flash('System error occurred. Please try again.', 'error')
        return render_template('index.html')


# ─────────────────────────────────────────────
#  Consultation API Routes
# ─────────────────────────────────────────────

@app.route('/api/doctors', methods=['GET'])
@login_required
def get_doctors():
    doctors = User.query.filter_by(role='doctor').all()
    results = []
    for doc in doctors:
        profile = doc.doctor_profile
        results.append({
            'id': doc.id,
            'name': doc.username,
            'specialization': profile.specialization if profile else 'General Physician',
            'experience': profile.experience_years if profile else 0,
            'availability': profile.availability_status if profile else 'Unknown'
        })
    return {'status': 'success', 'doctors': results}

@app.route('/request-consultation', methods=['POST'])
@login_required
def request_consultation():
    if current_user.role != 'patient':
        return {'status': 'error', 'message': 'Only patients can request consultations'}, 403
    
    data = request.get_json()
    doctor_id = data.get('doctor_id')
    message = data.get('message', '').strip()
    notes = data.get('notes', '').strip()
    
    if not doctor_id or not message:
        return {'status': 'error', 'message': 'Doctor ID and message are required'}, 400
        
    # Check if a pending request already exists
    existing = ConsultationRequest.query.filter_by(
        patient_id=current_user.id, 
        doctor_id=doctor_id, 
        status='Pending'
    ).first()
    
    if existing:
        return {'status': 'error', 'message': 'You already have a pending request with this doctor'}, 400
        
    new_req = ConsultationRequest(
        patient_id=current_user.id,
        doctor_id=doctor_id,
        request_message=message,
        additional_notes=notes
    )
    db.session.add(new_req)
    db.session.commit()
    
    return {'status': 'success', 'message': 'Consultation request sent successfully'}

@app.route('/patient-requests/<int:patient_id>', methods=['GET'])
@login_required
def get_patient_requests(patient_id):
    if current_user.role != 'patient' or current_user.id != patient_id:
        return {'status': 'error', 'message': 'Unauthorized'}, 403
        
    requests = ConsultationRequest.query.filter_by(patient_id=patient_id).order_by(ConsultationRequest.created_at.desc()).all()
    results = []
    for req in requests:
        doc_profile = req.doctor.doctor_profile
        results.append({
            'id': req.id,
            'doctor_name': req.doctor.username,
            'specialization': doc_profile.specialization if doc_profile else 'General Physician',
            'message': req.request_message,
            'status': req.status,
            'date': req.created_at.strftime('%Y-%m-%d %H:%M')
        })
    return {'status': 'success', 'requests': results}

@app.route('/doctor-requests/<int:doctor_id>', methods=['GET'])
@login_required
def get_doctor_requests(doctor_id):
    if current_user.role != 'doctor' or current_user.id != doctor_id:
        return {'status': 'error', 'message': 'Unauthorized'}, 403
        
    requests = ConsultationRequest.query.filter_by(doctor_id=doctor_id).order_by(ConsultationRequest.created_at.desc()).all()
    results = []
    for req in requests:
        results.append({
            'id': req.id,
            'patient_name': req.patient.username,
            'message': req.request_message,
            'notes': req.additional_notes,
            'status': req.status,
            'date': req.created_at.strftime('%Y-%m-%d %H:%M')
        })
    return {'status': 'success', 'requests': results}

@app.route('/update-request-status', methods=['POST'])
@login_required
def update_request_status():
    if current_user.role != 'doctor':
        return {'status': 'error', 'message': 'Unauthorized'}, 403
        
    data = request.get_json()
    request_id = data.get('request_id')
    new_status = data.get('status')
    
    if not request_id or new_status not in ['Accepted', 'Rejected', 'Completed']:
        return {'status': 'error', 'message': 'Invalid data'}, 400
        
    req = ConsultationRequest.query.get(request_id)
    if not req or req.doctor_id != current_user.id:
        return {'status': 'error', 'message': 'Request not found or unauthorized'}, 404
        
    req.status = new_status
    db.session.commit()
    
    return {'status': 'success', 'message': f'Request marked as {new_status}'}

# ─────────────────────────────────────────────
#  PDF Download Route (from history row)
# ─────────────────────────────────────────────

result_to_key = {
    'NORMAL': 0,
    'HYPERTENSION (Stage-1)': 1,
    'HYPERTENSION (Stage-2)': 2,
    'HYPERTENSIVE CRISIS': 3
}

@app.route('/download_report/record/<int:record_id>')
@login_required
def download_report_by_id(record_id):
    # Patients download their own; doctors download for linked patients
    if current_user.role == 'doctor':
        record = PredictionRecord.query.get_or_404(record_id)
        patient = User.query.get_or_404(record.user_id)
        if patient not in current_user.patients:
            flash('You do not have permission to access this record.', 'error')
            return redirect(url_for('doctor_dashboard'))
    else:
        record = PredictionRecord.query.filter_by(
            id=record_id, user_id=current_user.id
        ).first_or_404()

    rec_key = result_to_key.get(record.result, 0)
    rec     = recommendations[rec_key]

    # Decode stored assessment inputs
    assessment_data = None
    if record.input_data:
        try:
            assessment_data = json.loads(record.input_data)
        except Exception:
            pass

    return _build_pdf(
        prediction      = record.result,
        confidence      = f'{record.confidence:.1f}',
        priority        = record.priority,
        rec_title       = rec['title'],
        rec_desc        = rec['description'],
        rec_actions     = rec['actions'],
        date_str        = record.created_at.strftime('%B %d, %Y  %H:%M'),
        assessment_data = assessment_data,
        patient_user    = patient if current_user.role == 'doctor' else current_user
    )


@app.route('/download_report', methods=['POST'])
@login_required
def download_report():
    # Try to decode assessment data passed from the form
    assessment_data = None
    raw = request.form.get('input_data', '')
    if raw:
        try:
            assessment_data = json.loads(raw)
        except Exception:
            pass
    return _build_pdf(
        prediction      = request.form.get('prediction', 'N/A'),
        confidence      = request.form.get('confidence', 'N/A'),
        priority        = request.form.get('priority', ''),
        rec_title       = request.form.get('rec_title', ''),
        rec_desc        = request.form.get('rec_desc', ''),
        rec_actions     = request.form.getlist('rec_actions'),
        date_str        = datetime.utcnow().strftime('%B %d, %Y'),
        assessment_data = assessment_data,
        patient_user    = current_user
    )


def _build_pdf(prediction, confidence, priority,
               rec_title, rec_desc, rec_actions,
               date_str=None, assessment_data=None, patient_user=None):
    """Generate a comprehensive, doctor-ready PDF report."""
    if date_str is None:
        date_str = datetime.utcnow().strftime('%B %d, %Y')
    # Determine whose name goes on the report
    report_user = patient_user if patient_user else current_user

    # ── Human-readable label maps for stored form values ──────────
    LABEL_MAP = {
        'Gender':         {'0': 'Male', '1': 'Female'},
        'Age':            {'18-34': '18 – 34 years', '35-50': '35 – 50 years',
                           '51-64': '51 – 64 years', '65+': '65+ years'},
        'History':        {'Yes': 'Yes', 'No': 'No'},
        'Patient':        {'Yes': 'Yes (existing patient)', 'No': 'No'},
        'TakeMedication': {'Yes': 'Yes', 'No': 'No'},
        'Severity':       {'Mild': 'Mild', 'Moderate': 'Moderate', 'Severe': 'Severe'},
        'BreathShortness':{'Yes': 'Yes', 'No': 'No'},
        'VisualChanges':  {'Yes': 'Yes', 'No': 'No'},
        'NoseBleeding':   {'Yes': 'Yes', 'No': 'No'},
        'Whendiagnoused': {'<1 Year': 'Less than 1 year',
                           '1 - 5 Years': '1 – 5 years', '>5 Years': 'More than 5 years'},
        'Systolic':       {'100 - 110': '100 – 110 mmHg', '111 - 120': '111 – 120 mmHg',
                           '121 - 130': '121 – 130 mmHg', '130+': '130+ mmHg'},
        'Diastolic':      {'70 - 80': '70 – 80 mmHg', '81 - 90': '81 – 90 mmHg',
                           '91 - 100': '91 – 100 mmHg', '100+': '100+ mmHg'},
        'ControlledDiet': {'Yes': 'Yes', 'No': 'No'},
    }
    FIELD_LABELS = {
        'Gender':          'Gender',
        'Age':             'Age Group',
        'History':         'Family History of Hypertension',
        'Patient':         'Currently a Patient',
        'TakeMedication':  'Currently on Medication',
        'Severity':        'Symptom Severity',
        'BreathShortness': 'Shortness of Breath',
        'VisualChanges':   'Visual Changes',
        'NoseBleeding':    'Nose Bleeding',
        'Whendiagnoused':  'Duration Since Diagnosis',
        'Systolic':        'Systolic Blood Pressure',
        'Diastolic':       'Diastolic Blood Pressure',
        'ControlledDiet':  'Controlled / Low-Sodium Diet',
    }

    priority_colors = {
        'Low Risk': '#10B981', 'Moderate Risk': '#F59E0B',
        'High Risk': '#F97316', 'EMERGENCY': '#EF4444'
    }
    accent = priority_colors.get(priority, '#0891B2')

    # ── Canvas setup ──────────────────────────────────────────────
    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=A4)
    W, H = A4
    MARGIN = 40
    TEXT_W = W - MARGIN * 2

    # ── Helper: section heading ───────────────────────────────────
    def section_heading(canvas, y, title, color='#0F172A'):
        canvas.setFillColor(rl_colors.HexColor(color))
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawString(MARGIN, y, title)
        y -= 5
        canvas.setFillColor(rl_colors.HexColor(accent))
        canvas.rect(MARGIN, y - 2, TEXT_W, 1.5, fill=True, stroke=False)
        return y - 14

    # ── Helper: check page overflow ───────────────────────────────
    FOOTER_H = 65
    def check_page(canvas, y, needed=20):
        if y < FOOTER_H + needed:
            draw_footer(canvas)
            canvas.showPage()
            return H - 50
        return y

    # ── Helper: draw footer on current page ───────────────────────
    def draw_footer(canvas):
        canvas.setFillColor(rl_colors.HexColor('#F1F5F9'))
        canvas.rect(0, 0, W, FOOTER_H - 10, fill=True, stroke=False)
        canvas.setFillColor(rl_colors.HexColor('#64748B'))
        canvas.setFont('Helvetica', 7.5)
        canvas.drawString(MARGIN, 38, 'This report is a decision-support tool only — NOT a clinical diagnosis.')
        canvas.drawString(MARGIN, 24, 'Always consult a licensed healthcare professional before making any medical decisions.')
        canvas.drawRightString(W - MARGIN, 24, f'Generated: {date_str}')

    # ════════════════════════════════════════════════
    #  PAGE 1 — HEADER BANNER
    # ════════════════════════════════════════════════
    c.setFillColor(rl_colors.HexColor('#0891B2'))
    c.rect(0, H - 85, W, 85, fill=True, stroke=False)
    # Side accent strip
    c.setFillColor(rl_colors.HexColor('#06B6D4'))
    c.rect(0, H - 85, 6, 85, fill=True, stroke=False)

    c.setFillColor(rl_colors.white)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(MARGIN + 10, H - 38, 'Patient Health Report')
    c.setFont('Helvetica', 9)
    c.drawString(MARGIN + 10, H - 56, 'Hypertension Risk Assessment  |  Confidential Medical Document')
    c.setFont('Helvetica', 8)
    c.drawString(MARGIN + 10, H - 72, f'Patient: {report_user.username}   |   Generated: {date_str}')

    y = H - 105

    # ════════════════════════════════════════════════
    #  SECTION 1 — RISK SUMMARY BANNER
    # ════════════════════════════════════════════════
    c.setFillColor(rl_colors.HexColor(accent))
    c.rect(MARGIN, y - 44, TEXT_W, 48, fill=True, stroke=False)
    c.setFillColor(rl_colors.white)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(MARGIN + 12, y - 20, f'Diagnosis: {prediction}')
    c.setFont('Helvetica', 10)
    c.drawString(MARGIN + 12, y - 36, f'Confidence Score: {confidence}%     |     Risk Level: {priority.upper()}')
    y -= 62

    # ════════════════════════════════════════════════
    #  SECTION 2 — PATIENT DETAILS
    # ════════════════════════════════════════════════
    y -= 8
    y = section_heading(c, y, 'PATIENT DETAILS')
    y -= 4

    patient_rows = [
        ('Patient Name', report_user.username),
        ('Platform ID',  f'#{report_user.id:04d}'),
        ('Report Date',  date_str),
    ]
    # Pull basic demographics from assessment data if available
    if assessment_data:
        if 'Gender' in assessment_data:
            patient_rows.insert(1, ('Gender', assessment_data['Gender']))
        if 'Age' in assessment_data:
            patient_rows.insert(2, ('Age Group', LABEL_MAP['Age'].get(assessment_data['Age'], assessment_data['Age'])))

    for i, (lbl, val) in enumerate(patient_rows):
        row_bg = '#F8FAFC' if i % 2 == 0 else '#FFFFFF'
        c.setFillColor(rl_colors.HexColor(row_bg))
        c.rect(MARGIN, y - 14, TEXT_W, 18, fill=True, stroke=False)
        c.setFillColor(rl_colors.HexColor('#475569'))
        c.setFont('Helvetica-Bold', 9)
        c.drawString(MARGIN + 8, y - 9, lbl)
        c.setFillColor(rl_colors.HexColor('#0F172A'))
        c.setFont('Helvetica', 9)
        c.drawString(MARGIN + 160, y - 9, str(val))
        y -= 18
        y = check_page(c, y, 20)

    # ════════════════════════════════════════════════
    #  SECTION 3 — ASSESSMENT DETAILS
    # ════════════════════════════════════════════════
    if assessment_data:
        y -= 16
        y = check_page(c, y, 80)
        y = section_heading(c, y, 'ASSESSMENT DETAILS  (Input Parameters)')
        y -= 4

        # Column header row
        c.setFillColor(rl_colors.HexColor('#E2E8F0'))
        c.rect(MARGIN, y - 14, TEXT_W, 17, fill=True, stroke=False)
        c.setFillColor(rl_colors.HexColor('#334155'))
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(MARGIN + 8, y - 9,  'Parameter')
        c.drawString(MARGIN + 200, y - 9, 'Value')
        y -= 17

        # BP parameters first (most clinically relevant)
        priority_fields = ['Systolic', 'Diastolic', 'Gender', 'Age',
                           'Severity', 'TakeMedication', 'ControlledDiet',
                           'History', 'Patient', 'BreathShortness',
                           'VisualChanges', 'NoseBleeding', 'Whendiagnoused']

        for i, key in enumerate(priority_fields):
            val = assessment_data.get(key)
            if val is None:
                continue
            display_val = LABEL_MAP.get(key, {}).get(val, val)
            field_label = FIELD_LABELS.get(key, key)

            row_bg = '#F8FAFC' if i % 2 == 0 else '#FFFFFF'
            c.setFillColor(rl_colors.HexColor(row_bg))
            c.rect(MARGIN, y - 14, TEXT_W, 17, fill=True, stroke=False)

            c.setFillColor(rl_colors.HexColor('#475569'))
            c.setFont('Helvetica', 9)
            c.drawString(MARGIN + 8, y - 9, field_label)

            # Highlight BP values in accent colour
            val_color = accent if key in ('Systolic', 'Diastolic') else '#0F172A'
            c.setFillColor(rl_colors.HexColor(val_color))
            c.setFont('Helvetica-Bold' if key in ('Systolic', 'Diastolic') else 'Helvetica', 9)
            c.drawString(MARGIN + 200, y - 9, display_val)
            y -= 17
            y = check_page(c, y, 20)

    # ════════════════════════════════════════════════
    #  PAGE 2 — RISK ANALYSIS  (forced page break)
    # ════════════════════════════════════════════════
    draw_footer(c)
    c.showPage()

    # Page 2 mini-header
    c.setFillColor(rl_colors.HexColor('#0F172A'))
    c.rect(0, H - 36, W, 36, fill=True, stroke=False)
    c.setFillColor(rl_colors.HexColor(accent))
    c.rect(0, H - 36, 6, 36, fill=True, stroke=False)
    c.setFillColor(rl_colors.white)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(MARGIN + 6, H - 22, 'Patient Health Report  —  Clinical Analysis')
    c.setFont('Helvetica', 8)
    c.drawString(MARGIN + 6, H - 32, f'Patient: {report_user.username}   |   {date_str}')

    y = H - 56

    y = section_heading(c, y, 'RISK ANALYSIS')
    y -= 6

    c.setFillColor(rl_colors.HexColor('#0F172A'))
    c.setFont('Helvetica-Bold', 10)
    c.drawString(MARGIN, y, rec_title)
    y -= 16
    c.setFont('Helvetica', 9)
    c.setFillColor(rl_colors.HexColor('#475569'))
    for line in simpleSplit(rec_desc, 'Helvetica', 9, TEXT_W):
        y = check_page(c, y, 14)
        c.drawString(MARGIN, y, line)
        y -= 14

    # ════════════════════════════════════════════════
    #  SECTION 5 — CLINICAL RECOMMENDATIONS
    # ════════════════════════════════════════════════
    y -= 14
    y = check_page(c, y, 80)
    y = section_heading(c, y, 'CLINICAL RECOMMENDATIONS')
    y -= 6

    for idx, action in enumerate(rec_actions):
        y = check_page(c, y, 24)
        # bullet circle
        c.setFillColor(rl_colors.HexColor(accent))
        c.circle(MARGIN + 6, y - 3, 3.5, fill=True, stroke=False)
        # action text
        c.setFillColor(rl_colors.HexColor('#0F172A'))
        c.setFont('Helvetica', 9)
        lines = simpleSplit(action, 'Helvetica', 9, TEXT_W - 20)
        for li, line in enumerate(lines):
            y = check_page(c, y, 14)
            c.drawString(MARGIN + 16, y, line)
            y -= 13
        y -= 4

    # ── Draw footer on final page ──────────────────
    draw_footer(c)
    c.save()
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'health_report_{report_user.username}.pdf',
        mimetype='application/pdf'
    )




# ─────────────────────────────────────────────
#  Init DB & Run
# ─────────────────────────────────────────────
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)



