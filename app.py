from flask import Flask, render_template, request, flash, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin,
                         login_user, login_required,
                         logout_user, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import joblib, numpy as np, os, io
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
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    predictions   = db.relationship('PredictionRecord', backref='user', lazy=True)

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
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)


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
# def login():
#     if current_user.is_authenticated:
#         return redirect(url_for('home'))
#     if request.method == 'POST':
#         email    = request.form.get('email', '').strip()
#         password = request.form.get('password', '')
#         remember = bool(request.form.get('remember'))

#         if not email or not password:
#             flash('Please fill in all fields.', 'error')
#             return render_template('login.html')

#         user = User.query.filter_by(email=email).first()
#         if user and user.check_password(password):
#             login_user(user, remember=remember)
#             next_page = request.args.get('next')
#             flash(f'Welcome back, {user.username}!', 'success')
#             return redirect(next_page or url_for('home'))
#         else:
#             flash('Invalid email or password.', 'error')

#     return render_template('login.html')

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

        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash(f'Account created! Welcome, {username}.', 'success')
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
    from collections import defaultdict

    records_asc = (PredictionRecord.query
                   .filter_by(user_id=current_user.id)
                   .order_by(PredictionRecord.created_at.asc())
                   .all())

    total = len(records_asc)
    last  = records_asc[-1] if records_asc else None

    # ── Monthly chart (last 6 months) ─────────────
    monthly = defaultdict(int)
    for r in records_asc:
        monthly[r.created_at.strftime('%b %Y')] += 1
    month_labels = list(monthly.keys())[-6:]
    month_counts = [monthly[k] for k in month_labels]

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
        last3  = records_asc[-3:]
        scores = [severity_score.get(r.result, 0) for r in last3]
        if scores[-1] > scores[0]:
            trend_label = '⬆ Risk Increasing'
            trend_type  = 'up'
        elif scores[-1] < scores[0]:
            trend_label = '⬇ Improving'
            trend_type  = 'down'
        else:
            trend_label = '➡ Stable'
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
        month_labels = month_labels,
        month_counts = month_counts,
        trend_label  = trend_label,
        trend_type   = trend_type,
        insights     = insights,
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

        # Save to DB
        record = PredictionRecord(
            user_id    = current_user.id,
            result     = stage_map[prediction],
            confidence = round(confidence, 2),
            priority   = rec['priority']
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
#  PDF Download Route (from history row)
# ─────────────────────────────────────────────
# Map stored result string back to recommendations key
result_to_key = {
    'NORMAL': 0,
    'HYPERTENSION (Stage-1)': 1,
    'HYPERTENSION (Stage-2)': 2,
    'HYPERTENSIVE CRISIS': 3
}

@app.route('/download_report/record/<int:record_id>')
@login_required
def download_report_by_id(record_id):
    record = PredictionRecord.query.filter_by(
        id=record_id, user_id=current_user.id
    ).first_or_404()

    rec_key = result_to_key.get(record.result, 0)
    rec     = recommendations[rec_key]

    return _build_pdf(
        prediction  = record.result,
        confidence  = f'{record.confidence:.1f}',
        priority    = record.priority,
        rec_title   = rec['title'],
        rec_desc    = rec['description'],
        rec_actions = rec['actions'],
        date_str    = record.created_at.strftime('%B %d, %Y  %H:%M')
    )


@app.route('/download_report', methods=['POST'])
@login_required
def download_report():
    return _build_pdf(
        prediction  = request.form.get('prediction', 'N/A'),
        confidence  = request.form.get('confidence', 'N/A'),
        priority    = request.form.get('priority', ''),
        rec_title   = request.form.get('rec_title', ''),
        rec_desc    = request.form.get('rec_desc', ''),
        rec_actions = request.form.getlist('rec_actions'),
        date_str    = datetime.utcnow().strftime('%B %d, %Y')
    )


def _build_pdf(prediction, confidence, priority,
               rec_title, rec_desc, rec_actions, date_str=None):
    if date_str is None:
        date_str = datetime.utcnow().strftime('%B %d, %Y')

    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=A4)
    W, H = A4

    c.setFillColor(rl_colors.HexColor('#0891B2'))
    c.rect(0, H - 90, W, 90, fill=True, stroke=False)
    c.setFillColor(rl_colors.white)
    c.setFont('Helvetica-Bold', 20)
    c.drawString(40, H - 45, 'Hypertension Risk Assessment Report')
    c.setFont('Helvetica', 10)
    c.drawString(40, H - 65,
                 f'Generated for: {current_user.username}  |  Date: {date_str}')

    priority_colors = {
        'Low Risk': '#10B981', 'Moderate Risk': '#F59E0B',
        'High Risk': '#F97316', 'EMERGENCY': '#EF4444'
    }
    accent = priority_colors.get(priority, '#0891B2')

    c.setFillColor(rl_colors.HexColor(accent))
    c.rect(40, H - 165, W - 80, 55, fill=True, stroke=False)
    c.setFillColor(rl_colors.white)
    c.setFont('Helvetica-Bold', 15)
    c.drawString(55, H - 133, f'Diagnosis: {prediction}')
    c.setFont('Helvetica', 11)
    c.drawString(55, H - 153,
                 f'Confidence: {confidence}%   |   Risk Level: {priority}')

    y = H - 210
    c.setFillColor(rl_colors.HexColor('#0F172A'))
    c.setFont('Helvetica-Bold', 13)
    c.drawString(40, y, rec_title)
    y -= 22
    c.setFont('Helvetica', 10)
    c.setFillColor(rl_colors.HexColor('#475569'))
    for line in simpleSplit(rec_desc, 'Helvetica', 10, W - 80):
        c.drawString(40, y, line)
        y -= 16

    y -= 15
    c.setFillColor(rl_colors.HexColor('#0F172A'))
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y, 'Clinical Recommendations:')
    y -= 8
    c.setFillColor(rl_colors.HexColor(accent))
    c.rect(40, y - 4, W - 80, 1, fill=True, stroke=False)
    y -= 18
    c.setFont('Helvetica', 10)
    for action in rec_actions:
        c.setFillColor(rl_colors.HexColor(accent))
        c.circle(50, y + 3, 3, fill=True, stroke=False)
        c.setFillColor(rl_colors.HexColor('#0F172A'))
        for line in simpleSplit(action, 'Helvetica', 10, W - 110):
            c.drawString(62, y, line)
            y -= 15
        y -= 4

    c.setFillColor(rl_colors.HexColor('#F1F5F9'))
    c.rect(0, 0, W, 55, fill=True, stroke=False)
    c.setFillColor(rl_colors.HexColor('#64748B'))
    c.setFont('Helvetica', 8)
    c.drawString(40, 35, 'This report is a decision-support tool only — NOT a clinical diagnosis.')
    c.drawString(40, 20,
                 'Always consult a licensed healthcare professional before making any medical decisions.')
    c.save()
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'hypertension_report_{current_user.username}.pdf',
        mimetype='application/pdf'
    )




# ─────────────────────────────────────────────
#  Init DB & Run
# ─────────────────────────────────────────────
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
