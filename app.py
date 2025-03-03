import os
import math
import requests, csv
from io import StringIO
import openai
from flask import Flask, request, jsonify
from flask_cors import CORS

# ── WORKAROUND FOR FLASK-LOGIN / WERKZEUG COMPATIBILITY ──
# Flask-Login 0.6.2 expects werkzeug.urls.url_decode and url_encode,
# which were removed/changed in Werkzeug 3.x. Either pin Werkzeug to <3.0
# or apply the following monkey-patches.
import werkzeug.urls
if not hasattr(werkzeug.urls, 'url_decode'):
    # A simple fallback for url_decode (you may wish to implement a proper version)
    def url_decode(s, charset='utf-8'):
        return s
    werkzeug.urls.url_decode = url_decode

if not hasattr(werkzeug.urls, 'url_encode'):
    # Use Python's standard library function as a replacement
    from urllib.parse import urlencode as py_urlencode
    werkzeug.urls.url_encode = py_urlencode

# Now import Flask-Login after applying the monkey-patches
from flask_login import (
    LoginManager, login_user, login_required,
    logout_user, current_user, UserMixin
)

# ── APP INITIALIZATION ──
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "this-should-be-changed")
approved_origins = [
    "https://www.surprisegranite.com",
    "https://www.remodely.ai"
]
CORS(app, resources={r"/*": {"origins": approved_origins}})

# Set your OpenAI API key from environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("Missing OpenAI API Key. Please set it in environment variables.")
openai.api_key = OPENAI_API_KEY

# ── DATABASE & LOGIN MANAGER SETUP ──
login_manager = LoginManager(app)

# Example User model for Flask-Login (in a real app, use your database model)
class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

# A simple in-memory store for demo purposes.
users = {"testuser": User(id=1, username="testuser")}

@login_manager.user_loader
def load_user(user_id):
    for user in users.values():
        if str(user.id) == str(user_id):
            return user
    return None

# ── HELPER FUNCTION: GET PRICING DATA FROM CSV ──
def get_pricing_data():
    """
    Fetch pricing data from the published Google Sheets CSV.
    Expected CSV columns:
      Color Name, Vendor Name, Thickness, Material, size, Total/SqFt, Cost/SqFt, Price Group, Tier
    Uses the lowercased "Color Name" as the key.
    """
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

# ── BASIC ENDPOINTS ──
@app.route("/")
def home():
    return "<h1>Remodely AI Chatbot</h1><p>Your AI assistant is ready.</p>"

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_input = data.get("message", "")
    if not user_input:
        return jsonify({"error": "Missing user input"}), 400
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful remodeling assistant for Surprise Granite."},
                {"role": "user", "content": user_input}
            ]
        )
        return jsonify({"response": response.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── ESTIMATE ENDPOINT ──
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

        multiplier = 1.0
        if edge_detail.lower() == "premium":
            multiplier = 1.05
        elif edge_detail.lower() == "custom":
            multiplier = 1.10
        material_cost *= multiplier

        preliminary_total = material_cost + sink_cost + cooktop_cost + backsplash_cost
        effective_sq_ft = total_sq_ft * 1.20
        slab_count = math.ceil(effective_sq_ft / color_total_sqft)

        markup = 1.35 if job_type.lower() == "slab only" else 1.30
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

# ── MILLWORK ESTIMATE ENDPOINT ──
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

        style_multiplier = 1.2 if cabinet_style == "modern" else (1.1 if cabinet_style == "traditional" else 1.0)
        wood_multiplier = 1.3 if wood_type == "oak" else (1.2 if wood_type == "maple" else 1.0)
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

# ── USER REGISTRATION & LOGIN ENDPOINTS ──
@app.route("/api/register", methods=["POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"error": "Missing username or password"}), 400
    users[username] = User(id=len(users)+1, username=username)
    return jsonify({"message": "User registered successfully", "username": username}), 200

@app.route("/api/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"error": "Missing username or password"}), 400
    if username in users and password == "testpass":
        user = users[username]
        login_user(user)
        return jsonify({"message": "Login successful", "username": username}), 200
    else:
        return jsonify({"error": "Invalid credentials"}), 401

@app.route("/api/logout", methods=["POST", "OPTIONS"])
@login_required
def logout():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    logout_user()
    return jsonify({"message": "Logged out successfully"}), 200

# ── RUN THE APP ──
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
