from fastapi import FastAPI, UploadFile, File, Form, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from src.database import SessionLocal, init_db, User
from src.core import embed_watermark, extract_watermark
from src.utils import load_image, save_image, text_to_binary, binary_to_text
import shutil
import os
import numpy as np
import random                 # NEW: For noise generation
from PIL import Image, ImageFilter # NEW: For attack simulation

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
    """Creates a new user with a random Base Key if they don't exist"""
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return {"status": "error", "message": "User already exists!"}
    
    new_user = User(username=username, secret_key_data=None)
    db.add(new_user)
    db.commit()
    return {"status": "success", "message": f"Welcome, {username}!"}

@app.post("/stamp")
async def stamp_image(
    username: str = Form(...), 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    """
    Stamps the image and forces PNG output.
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return {"error": "User not found. Register first."}

    # 1. Save Temp File
    file_location = f"static/uploads/{file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # 2. Load & Embed
    original = load_image(file_location)
    watermark_text = f"User:{username}"
    
    # 3. Apply NeuroStamp (Alpha=80 + Scrambling with username)
    watermarked_img, key_coeffs = embed_watermark(original, watermark_text, alpha=40, username=username)    
    
    # 4. Save Key to DB
    user.secret_key_data = key_coeffs
    db.commit()
    
    # 5. Save Output (FORCE PNG)
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
    Verifies the image owner.
    """
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.secret_key_data:
        return {"error": "User not found or no key registered."}
        
    # 1. Save Temp
    file_location = f"static/uploads/verify_{file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # 2. Extract
    suspicious = load_image(file_location)
    expected_text = f"User:{username}"
    expected_bits = len(expected_text) * 8
    
    # 3. Decode (Alpha=80 + Scrambling with username)
    recovered_bits = extract_watermark(suspicious, user.secret_key_data, alpha=40, length=expected_bits, username=username)
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

# --- NEW ATTACK SIMULATION ROUTE ---

@app.post("/attack")
async def attack_image(
    filename: str = Form(...),
    attack_type: str = Form(...)
):
    """
    Simulates an attack on an existing file in static/uploads.
    Returns the path to the damaged file.
    """
    file_path = f"static/uploads/{filename}"
    if not os.path.exists(file_path):
        return {"error": "File not found"}
    
    # Load using standard PIL to manipulate
    img = Image.open(file_path).convert("RGB")
    
    # 1. ATTACK: NOISE (Simulate bad sensor/ISO grain)
    if attack_type == "noise":
        arr = np.array(img)
        # Add random Gaussian noise
        noise = np.random.normal(0, 25, arr.shape) # Standard Deviation = 25
        arr = arr + noise
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
        
    # 2. ATTACK: BLUR (Updated to Realistic "Soft Focus")
    elif attack_type == "blur":
        # Radius 0.8 simulates a slightly out-of-focus lens, which is a fair test.
        # Radius 1.5+ is an intentional destruction attack.
        img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
        
    # 3. ATTACK: COMPRESSION (Updated to Standard Web Quality)
    elif attack_type == "jpeg":
        # Quality 75 is the standard "High" quality for JPEG (WhatsApp/Web).
        # Quality 50 introduces heavy blocking artifacts that destroy DWT data.
        temp_jpg = f"static/uploads/temp_attack_{filename}.jpg"
        img.save(temp_jpg, "JPEG", quality=75)
        img = Image.open(temp_jpg).convert("RGB")
        
    # Save the attacked version as PNG (so we don't accidentally damage it further)
    attack_filename = f"attacked_{attack_type}_{filename}"
    attack_path = f"static/uploads/{attack_filename}"
    
    img.save(attack_path, "PNG")
    
    return {
        "status": "success", 
        "attack_url": f"/static/uploads/{attack_filename}",
        "filename": attack_filename
    }