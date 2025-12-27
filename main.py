from fastapi import FastAPI, UploadFile, File, Form, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

# Import the new DB structure
from src.database import SessionLocal, init_db, User, ImageRegistry

# Import utils (Ensure compute_dhash and calculate_hamming_distance are in src/utils.py)
from src.utils import (
    load_image,
    save_image,
    text_to_binary,
    binary_to_text,
    compute_dhash,
    calculate_hamming_distance,
)

from src.core import embed_watermark, extract_watermark
import shutil
import os
import numpy as np
import uuid  # For generating unique User IDs
from PIL import Image, ImageFilter

app = FastAPI()

# 1. Setup Static Folders & Templates
os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 2. Initialize DB on startup
init_db()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the Home Page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/register")
async def register_user(username: str = Form(...), db: Session = Depends(get_db)):
    """Creates a new user with a STRONG UUID"""
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return {"status": "error", "message": "User already exists!"}
    
    # Generate a random 12-char ID (e.g., 'a1b2c3d4e5f6')
    random_id = str(uuid.uuid4())[:12]
    
    # Save to DB (Key is None initially)
    new_user = User(username=username, user_uid=random_id)
    db.add(new_user)
    db.commit()
    return {"status": "success", "message": f"Welcome {username}! Your Secure ID is: {random_id}"}

@app.post("/stamp")
async def stamp_image(
    username: str = Form(...), 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    """
    Stamps image with UUID and Encrypts the key.
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return {"error": "User not found. Register first."}

    # 1. Save Temp File
    file_location = f"static/uploads/{file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    original = load_image(file_location)
    
    # --- SECURITY CHECK: PERCEPTUAL HASH (Prevent Double Spending) ---
    img_hash = compute_dhash(original)
    
    all_records = db.query(ImageRegistry).all()
    existing_record = None
    
    for record in all_records:
        distance = calculate_hamming_distance(img_hash, record.image_hash)
        # Threshold 10 for "Fuzzy Matching"
        if distance < 10:
            existing_record = record
            break
            
    if existing_record:
        # Check if the UUID matches
        if existing_record.owner_uid != user.user_uid:
            # FIND THE REAL NAME OF THE OWNER
            original_owner = db.query(User).filter(User.user_uid == existing_record.owner_uid).first()
            owner_name = original_owner.username if original_owner else "Unknown"
            
            return {
                "status": "error", 
                "error": f"COPYRIGHT CONFLICT: This image is already registered to '{owner_name}'."
            }
    # ----------------------------------------------------------------

    # 2. Embed Watermark (Using UUID)
    # Text is now "ID:a1b2c3..." instead of "User:Reneto"
    watermark_text = f"ID:{user.user_uid}"
    
    # Use Alpha 40 for Y-Channel
    watermarked_img, key_coeffs = embed_watermark(original, watermark_text, alpha=40, username=username)    
    
    # 3. Save Encrypted Key
    user.set_key_data(key_coeffs)
    
    # 4. Register the Hash
    if not existing_record:
        new_record = ImageRegistry(image_hash=img_hash, owner_uid=user.user_uid)
        db.add(new_record)
        
    db.commit()
    
    # 5. Save Output
    base_name = os.path.splitext(file.filename)[0]
    output_filename = f"stamped_{base_name}.png"
    output_path = f"static/uploads/{output_filename}"
    
    save_image(watermarked_img, output_path)
    
    return {"status": "success", "download_url": f"/static/uploads/{output_filename}"}

@app.post("/verify")
async def verify_image(
    username: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Verifies the image owner using Encrypted Key.
    """
    user = db.query(User).filter(User.username == username).first()
    
    # DECRYPT THE KEY
    decrypted_key = user.get_key_data() if user else None
    
    if not user or not decrypted_key:
        return {"error": "User not found or no key registered."}
        
    # 1. Save Temp
    file_location = f"static/uploads/verify_{file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # 2. Extract
    suspicious = load_image(file_location)
    
    # Expect the UUID, not the name
    expected_text = f"ID:{user.user_uid}"
    expected_bits = len(expected_text) * 8
    
    # 3. Decode
    recovered_bits = extract_watermark(suspicious, decrypted_key, alpha=40, length=expected_bits, username=username)
    recovered_text = binary_to_text(recovered_bits)
    
    # --- DEBUG PRINT ---
    print(f"\n🕵️ VERIFICATION DEBUG:")
    print(f"   Expected: {expected_text}")
    print(f"   Extracted: {recovered_text}")
    print(f"   Match: {recovered_text == expected_text}\n")
    # -------------------
    
    is_match = (recovered_text == expected_text)
    
    return {
        "status": "complete",
        "extracted_text": recovered_text,
        "is_match": is_match,
        "owner": username if is_match else "Unknown"
    }

# --- IMPROVED ROUTE: DATABASE VIEWER (With Raw Data Proof) ---
@app.get("/db-viewer", response_class=HTMLResponse)
async def view_database(request: Request, db: Session = Depends(get_db)):
    """
    ADMIN DASHBOARD: Shows the raw database tables with proof of encryption.
    """
    users = db.query(User).all()
    registry = db.query(ImageRegistry).all()
    
    html_content = """
    <html>
    <head>
        <title>NeuroStamp DB Viewer</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            .encrypted-text { 
                font-family: 'Courier New', monospace; 
                color: #d63384; 
                font-size: 0.9em; 
                word-break: break-all;
            }
            .uuid-text {
                font-family: 'Courier New', monospace; 
                color: #0d6efd; 
                font-weight: bold;
            }
        </style>
    </head>
    <body class="p-4 bg-light">
        <div class="container">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h2>📂 NeuroStamp Database Monitor</h2>
                <a href="/" class="btn btn-secondary">← Back to Dashboard</a>
            </div>
            
            <div class="card mb-5 shadow-sm">
                <div class="card-header bg-dark text-white">
                    <h5 class="m-0">User Credentials & Keys</h5>
                </div>
                <div class="card-body p-0">
                    <table class="table table-striped table-hover mb-0">
                        <thead class="table-dark">
                            <tr>
                                <th>Username</th>
                                <th>Public UUID (Identifier)</th>
                                <th>Secret Key Data (Stored in DB)</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    for u in users:
        if u.encrypted_key_data:
            # Show the first 30 chars of the encrypted bytes to prove it's scrambled
            # It will look like: b'gAAAAABl8...'
            raw_preview = str(u.encrypted_key_data)[:40] + "..."
            key_display = f"<span class='encrypted-text'>{raw_preview}</span>"
        else:
            key_display = "<span class='text-muted'>❌ No Key Generated</span>"
            
        html_content += f"""
            <tr>
                <td class="fw-bold">{u.username}</td>
                <td><span class="uuid-text">{u.user_uid}</span></td>
                <td>{key_display}</td>
            </tr>
        """
        
    html_content += """
                        </tbody>
                    </table>
                </div>
            </div>
            
            <div class="card shadow-sm">
                <div class="card-header bg-success text-white">
                    <h5 class="m-0">Image Ownership Registry</h5>
                </div>
                <div class="card-body p-0">
                    <table class="table table-striped table-hover mb-0">
                        <thead class="table-success">
                            <tr>
                                <th>ID</th>
                                <th>Visual Fingerprint (dHash)</th>
                                <th>Owner UUID</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    for r in registry:
        html_content += f"""
            <tr>
                <td>{r.id}</td>
                <td><code>{r.image_hash}</code></td>
                <td><span class="uuid-text">{r.owner_uid}</span></td>
            </tr>
        """
        
    html_content += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# --- ATTACK SIMULATION ROUTE ---
@app.post("/attack")
async def attack_image(
    filename: str = Form(...),
    attack_type: str = Form(...)
):
    """
    Simulates attacks.
    """
    file_path = f"static/uploads/{filename}"
    if not os.path.exists(file_path):
        return {"error": "File not found"}
    
    img = Image.open(file_path).convert("RGB")
    
    # 1. ATTACK: NOISE
    if attack_type == "noise":
        arr = np.array(img).astype(np.float32)
        noise = np.random.normal(0, 25, arr.shape)
        arr = arr + noise
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
        
    # 2. ATTACK: BLUR
    elif attack_type == "blur":
        img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
        
    # 3. ATTACK: COMPRESSION
    elif attack_type == "jpeg":
        temp_jpg = f"static/uploads/temp_attack_{filename}.jpg"
        img.save(temp_jpg, "JPEG", quality=75)
        img = Image.open(temp_jpg).convert("RGB")
        
    # 4. ATTACK: ROTATION
    elif attack_type == "rotate":
        img = img.rotate(5, expand=False)
        
    # 5. ATTACK: CROPPING
    elif attack_type == "crop":
        w, h = img.size
        left, top, right, bottom = w * 0.1, h * 0.1, w * 0.9, h * 0.9
        img = img.crop((left, top, right, bottom))
        
    else:
        return {"error": f"Unknown attack type: {attack_type}"}

    attack_filename = f"attacked_{attack_type}_{filename}"
    attack_path = f"static/uploads/{attack_filename}"
    img.save(attack_path, "PNG")
    
    return {
        "status": "success", 
        "attack_url": f"/static/uploads/{attack_filename}",
        "filename": attack_filename
    }