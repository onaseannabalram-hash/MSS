import os
from datetime import datetime, date
from io import BytesIO
from flask import Flask, render_template, redirect, url_for, request, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from weasyprint import HTML

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///maximatic_payroll.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ---------------- Models ----------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(120), unique=True, index=True)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default='clerk')  # 'admin' or 'clerk'

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id_code = db.Column(db.String(20), unique=True, index=True)
    first_name = db.Column(db.String(120))
    last_name = db.Column(db.String(120))
    department = db.Column(db.String(120))
    position = db.Column(db.String(120))
    email = db.Column(db.String(120))
    nis_number = db.Column(db.String(50))
    tax_reference = db.Column(db.String(50))
    bank_name = db.Column(db.String(100))
    bank_account = db.Column(db.String(100))
    # ✅ new field
    date_of_birth = db.Column(db.Date, nullable=True)

    # optional helper property
    @property
    def age(self):
        if self.date_of_birth:
            today = date.today()
            return (
                today.year
                - self.date_of_birth.year
                - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
            )
        return None
        
from flask_login import login_required

@app.route('/employee/<int:emp_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_employee(emp_id):
    emp = Employee.query.get_or_404(emp_id)

    if request.method == 'POST':
        emp.employee_id_code = request.form.get('employee_id_code')
        emp.first_name = request.form.get('first_name')
        emp.last_name = request.form.get('last_name')
        emp.department = request.form.get('department')
        emp.position = request.form.get('position')
        emp.email = request.form.get('email')
        emp.nis_number = request.form.get('nis_number')
        emp.tax_reference = request.form.get('tax_reference')
        emp.bank_name = request.form.get('bank_name')
        emp.bank_account = request.form.get('bank_account')

        # ✅ handle date of birth
        dob_str = request.form.get('date_of_birth')
        if dob_str:
            emp.date_of_birth = datetime.strptime(dob_str, "%Y-%m-%d").date()

        db.session.commit()
        flash('Employee updated successfully!')
        return redirect(url_for('dashboard'))  # ✅ go back to dashboard after saving

    # ✅ show the edit form when accessed via GET
    return render_template('edit_employee.html', employee=emp)


    
class Payslip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'))
    employee = db.relationship('Employee', backref='payslips')
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    basic_pay = db.Column(db.Float, default=0)
    overtime_pay = db.Column(db.Float, default=0)
    allowances = db.Column(db.Float, default=0)
    tax = db.Column(db.Float, default=0)
    nis = db.Column(db.Float, default=0)
    other_deductions = db.Column(db.Float, default=0)
    net_pay = db.Column(db.Float, default=0)
    hours_worked = db.Column(db.Float, default=0)       # NEW
    rate_per_hour = db.Column(db.Float, default=0)      # NEW
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    date_of_birth = db.Column(db.Date, nullable=True)   # ✅ new field

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---------------- Payroll Logic ----------------
def parse_float(form, name):
    try:
        return float(form.get(name, 0) or 0)
    except Exception:
        return 0
        

def compute_payroll(employee, basic, overtime, allowances, hours_worked=0, rate_per_hour=0):
    # Calculate hourly earnings
    hourly_total = hours_worked * rate_per_hour
    gross = basic + overtime + allowances + hourly_total

    # ✅ Use the age property you already defined
    if employee.age is not None and employee.age < 60:
        nis = round(gross * 0.056, 2)   # 5.6% deduction
    else:
        nis = 0.0                       # exempt if 60+

    # GRA tax: if gross > 130,000
    tax = 0
    if gross > 130000:
        taxable_amount = gross - 130000
        tax = taxable_amount * 0.28  # adjust rate if needed

    net = gross - (nis + tax)
    return gross, nis, tax, net


def ytd_for_employee(employee_id, period_end):
    start_of_year = date(period_end.year, 1, 1)
    slips = Payslip.query.filter(
        Payslip.employee_id == employee_id,
        Payslip.period_end >= start_of_year,
        Payslip.period_end <= period_end
    ).all()
    totals = {
        'basic_pay': 0.0, 'overtime_pay': 0.0, 'allowances': 0.0,
        'tax': 0.0, 'nis': 0.0, 'other_deductions': 0.0, 'net_pay': 0.0
    }
    for s in slips:
        totals['basic_pay'] += s.basic_pay
        totals['overtime_pay'] += s.overtime_pay
        totals['allowances'] += s.allowances
        totals['tax'] += s.tax
        totals['nis'] += s.nis
        totals['other_deductions'] += s.other_deductions
        totals['net_pay'] += s.net_pay
    return totals

# ---------------- CLI ----------------
@app.cli.command('initdb')
def initdb():
    db.create_all()
    if not User.query.filter_by(email='admin@maximatic.com').first():
        admin = User(name='Admin', email='admin@maximatic.com', role='admin')
        admin.set_password('ChangeMe123!')
        db.session.add(admin)
        db.session.commit()
    print("Database initialized and admin user created (admin@maximatic.com / ChangeMe123!).")

# ---------------- Auth ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(email=request.form['email']).first()
        if u and u.check_password(request.form['password']):
            login_user(u)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html', brand='Maximatic Security Services')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ---------------- Dashboard ----------------
@app.route('/')
@login_required
def dashboard():
    employees = Employee.query.order_by(Employee.last_name).all()
    recent = Payslip.query.order_by(Payslip.created_at.desc()).limit(10).all()
    return render_template('dashboard.html', employees=employees, payslips=recent, brand='Maximatic Security Services')

# ---------------- Employee CRUD ----------------
@app.route('/employees/new', methods=['GET', 'POST'])
@login_required
def employee_new():
    if request.method == 'POST':
        e = Employee(
            employee_id_code=request.form['employee_id_code'],
            first_name=request.form['first_name'],
            last_name=request.form['last_name'],
            department=request.form['department'],
            position=request.form['position'],
            email=request.form['email'],
            nis_number=request.form.get('nis_number'),
            tax_reference=request.form.get('tax_reference'),
            bank_name=request.form.get('bank_name'),
            bank_account=request.form.get('bank_account'),
            date_of_birth=dob  # ✅ added here
        )
        db.session.add(e); db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('employee_form.html', brand='Maximatic Security Services')

# ---------------- Payslip ----------------
@app.route('/payslips/new/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def payslip_new(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    if request.method == 'POST':
        basic = parse_float(request.form, 'basic_pay')
        overtime = parse_float(request.form, 'overtime_pay')
        allowances = parse_float(request.form, 'allowances')
        hours_worked = parse_float(request.form, 'hours_worked')   # NEW
        rate_per_hour = parse_float(request.form, 'rate_per_hour') # NEW

        # Updated payroll calculation to include hours × rate
        gross, nis, tax, net = compute_payroll(
            employee,
            basic, overtime, allowances,
            hours_worked, rate_per_hour
        )

        p = Payslip(
            employee=employee,
            period_start=date.fromisoformat(request.form['period_start']),
            period_end=date.fromisoformat(request.form['period_end']),
            basic_pay=basic,
            overtime_pay=overtime,
            allowances=allowances,
            tax=tax,
            nis=nis,
            other_deductions=0,
            net_pay=net,
            hours_worked=hours_worked,       # NEW
            rate_per_hour=rate_per_hour,     # NEW
            created_by=current_user.id
        )
        db.session.add(p)
        db.session.commit()
        return redirect(url_for('payslip_view', payslip_id=p.id))
    return render_template('payslip_form.html', employee=employee, brand='Maximatic Security Services')
    
    
@app.route('/payslips/<int:payslip_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_payslip(payslip_id):
    payslip = Payslip.query.get_or_404(payslip_id)

    if request.method == 'POST':
        payslip.basic_pay = parse_float(request.form, 'basic_pay')
        payslip.overtime_pay = parse_float(request.form, 'overtime_pay')
        payslip.allowances = parse_float(request.form, 'allowances')
        payslip.hours_worked = parse_float(request.form, 'hours_worked')
        payslip.rate_per_hour = parse_float(request.form, 'rate_per_hour')
        payslip.tax = parse_float(request.form, 'tax')
        payslip.nis = parse_float(request.form, 'nis')
        payslip.other_deductions = parse_float(request.form, 'other_deductions')
        payslip.net_pay = parse_float(request.form, 'net_pay')

        # ✅ update period dates
        payslip.period_start = date.fromisoformat(request.form['period_start'])
        payslip.period_end = date.fromisoformat(request.form['period_end'])

        db.session.commit()
        flash('Payslip updated successfully!')
        return redirect(url_for('payslip_view', payslip_id=payslip.id))

    return render_template('edit_payslip.html', payslip=payslip)



@app.route('/payslips/<int:payslip_id>')
@login_required
def payslip_view(payslip_id):
    p = Payslip.query.get_or_404(payslip_id)
    ytd = ytd_for_employee(p.employee_id, p.period_end)
    return render_template('payslip_view.html', p=p, ytd=ytd, brand='Maximatic Security Services')

@app.route('/payslips/<int:payslip_id>/pdf')
@login_required
def payslip_pdf(payslip_id):
    p = Payslip.query.get_or_404(payslip_id)
    ytd = ytd_for_employee(p.employee_id, p.period_end)
    html = render_template('payslip_pdf.html', p=p, ytd=ytd, brand='Maximatic Security Services')
    pdf_bytes = BytesIO()
    # ✅ Added base_url so static/logo.png can be loaded in the PDF
    HTML(string=html, base_url=request.url_root).write_pdf(pdf_bytes)
    pdf_bytes.seek(0)
    filename = f"Payslip_{p.employee.last_name}_{p.period_end.isoformat()}.pdf"
    return send_file(
        pdf_bytes,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )

if __name__ == '__main__':
    app.run(debug=True)

