from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
import os
import openai
from flask_cors import CORS
import requests, csv
from io import StringIO
import math

# Additional imports for authentication and password reset
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'  # change to a secure random value
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///remodely.db'  # or your chosen DB
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configure Flask-Mail (adjust these to your email server settings)
app.config['MAIL_SERVER'] = 'smtp.example.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your-email@example.com'
app.config['MAIL_PASSWORD'] = 'your-email-password'
mail = Mail(app)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'  # name of the login route

# Serializer for generating reset tokens
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# Approved domains exactly as they appear in the browser
approved_origins = [
    "https://www.surprisegranite.com",
    "https://www.remodely.ai"
]
CORS(app, resources={r"/*": {"origins": approved_origins}})

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("Missing OpenAI API Key. Please set it in environment variables.")
openai.api_key = OPENAI_API_KEY

# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(150), nullable=False)
    # Add other fields like name, company info, etc.
    # For storing quotes, you might have a relationship to a Quote model

class Quote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Helper Functions ---
def get_pricing_data():
    url = ("https://docs.google.com/spreadsheets/d/e/"
           "2PACX-1vRWyYuTQxC8_fKNBg9_aJiB7NMFztw6mgdhN35lo8sRL45MvncRg4D217lopZxuw39j5aJTN6TP4Elh"
           "/pub?output=csv")
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception("Could not fetch pricing data")
    csv_text = response.text
    csv_file = StringIO(csv_text)
    reader = csv.DictReader(csv_file)
    pricing = {}
    for row in reader:
        color = row["Color Name"].strip().lower()
        try:
            cost_sqft = float(row["Cost/SqFt"])
        except Exception:
            cost_sqft = 50.0
        try:
            color_total_sqft = float(row["Total/SqFt"])
        except Exception:
            color_total_sqft = 100.0
        pricing[color] = {"cost": cost_sqft, "total_sqft": color_total_sqft}
    return pricing

# --- ROUTES ---

@app.route("/")
def home():
    return "<h1>Remodely AI Chatbot</h1><p>Your AI assistant is ready.</p>"

# --- Authentication Routes ---
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        if User.query.filter_by(email=email).first():
            flash("Email already exists.")
            return redirect(url_for('signup'))
        new_user = User(email=email, password_hash=generate_password_hash(password))
        db.session.add(new_user)
        db.session.commit()
        flash("Sign-up successful. Please log in.")
        return redirect(url_for('login'))
    return render_template("signup.html")  # Create a signup.html template

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash("Invalid credentials.")
        return redirect(url_for('login'))
    return render_template("login.html")  # Create a login.html template

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Forgot Password ---
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email")
        user = User.query.filter_by(email=email).first()
        if user:
            token = s.dumps(email, salt='password-reset-salt')
            reset_link = url_for('reset_password', token=token, _external=True)
            # Send email with reset_link
            msg = Message("Password Reset Request", sender=app.config['MAIL_USERNAME'], recipients=[email])
            msg.body = f"Please click the following link to reset your password: {reset_link}"
            mail.send(msg)
            flash("Password reset link sent to your email.")
            return redirect(url_for('login'))
        flash("Email not found.")
    return render_template("forgot_password.html")  # Create a forgot_password.html template

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        return "The token is expired.", 400
    except BadSignature:
        return "Invalid token.", 400

    if request.method == "POST":
        new_password = request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user:
            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash("Password reset successful. Please log in.")
            return redirect(url_for('login'))
    return render_template("reset_password.html")  # Create a reset_password.html template

# --- Dashboard ---
@app.route("/dashboard")
@login_required
def dashboard():
    quotes = Quote.query.filter_by(user_id=current_user.id).all()
    return render_template("dashboard.html", quotes=quotes)  # Create a dashboard.html template

# --- API Routes (for estimator and millwork, etc.) ---
@app.route("/api/estimate", methods=["POST", "OPTIONS"])
def estimate():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.json
    if not data or not data.get("totalSqFt"):
        return jsonify({"error": "Missing project data"}), 400
    try:
        total_sq_ft = float(data.get("totalSqFt"))
        vendor = data.get("vendor", "default vendor")
        color = data.get("color", "").strip().lower()
        demo = data.get("demo", "no")
        sink_qty = float(data.get("sinkQty", 0))
        cooktop_qty = float(data.get("cooktopQty", 0))
        sink_type = data.get("sinkType", "standard")
        cooktop_type = data.get("cooktopType", "standard")
        backsplash = data.get("backsplash", "no")
        edge_detail = data.get("edgeDetail", "standard")
        job_name = data.get("jobName", "N/A")
        job_type = data.get("jobType", "fabricate and install")
        customer_name = data.get("customerName", "Valued Customer")

        pricing_data = get_pricing_data()
        pricing_info = pricing_data.get(color, {"cost": 50, "total_sqft": 100})
        price_per_sqft = pricing_info["cost"]
        color_total_sqft = pricing_info["total_sqft"]

        material_cost = total_sq_ft * price_per_sqft
        if demo.lower() == "yes":
            material_cost *= 1.10
        sink_cost = sink_qty * (150 if sink_type.lower() == "premium" else 100)
        cooktop_cost = cooktop_qty * (160 if cooktop_type.lower() == "premium" else 120)
        backsplash_cost = total_sq_ft * 20 if backsplash.lower() == "yes" else 0

        if edge_detail.lower() == "premium":
            multiplier = 1.05
        elif edge_detail.lower() == "custom":
            multiplier = 1.10
        else:
            multiplier = 1.0
        material_cost *= multiplier

        preliminary_total = material_cost + sink_cost + cooktop_cost + backsplash_cost
        effective_sq_ft = total_sq_ft * 1.20
        slab_count = math.ceil(effective_sq_ft / color_total_sqft)

        if job_type.lower() == "slab only":
            markup = 1.35
        else:
            markup = 1.30
        base_labor_rate = 45
        labor_cost = total_sq_ft * base_labor_rate * markup

        total_project_cost = preliminary_total + labor_cost
        final_cost_per_sqft = f"{(total_project_cost / total_sq_ft):.2f}" if total_sq_ft else "0.00"

        prompt = (
            f"Surprise Granite Detailed Estimate\n\n"
            f"Customer: Mr./Ms. {customer_name}\n"
            f"Job Name: {job_name}\n"
            f"Job Type: {job_type}\n"
            f"Project Area: {total_sq_ft} sq ft (with 20% waste: {effective_sq_ft:.2f} sq ft)\n"
            f"Vendor: {vendor}\n"
            f"Material (Color): {color.title()}\n"
            f"Price per Sq Ft for {color.title()}: ${price_per_sqft:.2f}\n"
            f"Material Cost: ${material_cost:.2f}\n"
            f"Sink Count: {sink_qty} ({sink_type}), Cost: ${sink_cost:.2f}\n"
            f"Cooktop Count: {cooktop_qty} ({cooktop_type}), Cost: ${cooktop_cost:.2f}\n"
            f"Backsplash Cost: ${backsplash_cost:.2f}\n"
            f"Number of Slabs Needed: {slab_count} (Each slab: {color_total_sqft} sq ft)\n"
            f"Preliminary Total (Materials): ${preliminary_total:.2f}\n"
            f"Labor Cost (at base rate ${base_labor_rate} per sq ft with markup {int((markup-1)*100)}%): ${labor_cost:.2f}\n"
            f"Total Project Cost: ${total_project_cost:.2f}\n"
            f"Final Cost Per Sq Ft: ${final_cost_per_sqft}\n\n"
            "Using the above pricing details from Surprise Granite, generate a comprehensive, professional, "
            "and detailed written estimate that includes a breakdown of material and labor costs, installation notes, "
            "and a personalized closing message addressing the customer by name. "
            "Ensure that the estimate is specific to Surprise Granite pricing and does not include generic information."
        )

        ai_response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert estimator at Surprise Granite. Provide a highly detailed and professional estimate strictly based on Surprise Granite pricing details."},
                {"role": "user", "content": prompt}
            ]
        )
        narrative = ai_response.choices[0].message.content

        return jsonify({
            "preliminary": {
                "material_cost": material_cost,
                "sink_cost": sink_cost,
                "cooktop_cost": cooktop_cost,
                "backsplash_cost": backsplash_cost,
                "labor_cost": labor_cost,
                "preliminary_total": preliminary_total,
                "slab_count": slab_count
            },
            "estimate": narrative
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/millwork-estimate", methods=["POST", "OPTIONS"])
def millwork_estimate():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.json
    required_fields = ["roomLength", "roomWidth", "cabinetStyle", "woodType"]
    for field in required_fields:
        if not data.get(field):
            return jsonify({"error": f"Missing {field}"}), 400
    try:
        room_length = float(data.get("roomLength"))
        room_width = float(data.get("roomWidth"))
        cabinet_style = data.get("cabinetStyle").strip().lower()
        wood_type = data.get("woodType").strip().lower()
        area = room_length * room_width
        base_cost = 50.0
        style_multiplier = 1.0
        if cabinet_style == "modern":
            style_multiplier = 1.2
        elif cabinet_style == "traditional":
            style_multiplier = 1.1
        wood_multiplier = 1.0
        if wood_type == "oak":
            wood_multiplier = 1.3
        elif wood_type == "maple":
            wood_multiplier = 1.2
        estimated_cost = area * base_cost * style_multiplier * wood_multiplier
        prompt = (
            f"Millwork Estimate Details:\n"
            f"Room dimensions: {room_length} ft x {room_width} ft (Area: {area} sq ft)\n"
            f"Cabinet Style: {cabinet_style.title()}\n"
            f"Wood Type: {wood_type.title()}\n"
            f"Base cost per sq ft: ${base_cost:.2f}\n"
            f"Style Multiplier: {style_multiplier}\n"
            f"Wood Multiplier: {wood_multiplier}\n"
            f"Calculated Estimated Cost: ${estimated_cost:.2f}\n\n"
            "Please provide a comprehensive, professional, and friendly written estimate for millwork services based on the above details."
        )
        ai_response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a professional millwork estimator."},
                {"role": "user", "content": prompt}
            ]
        )
        narrative = ai_response.choices[0].message.content
        return jsonify({
            "area": area,
            "estimatedCost": estimated_cost,
            "narrative": narrative
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
