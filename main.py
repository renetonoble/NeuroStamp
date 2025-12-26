from fastapi import FastAPI, UploadFile, File, Form, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

# Import the new table (ImageRegistry)
from src.database import SessionLocal, init_db, User, ImageRegistry

# Import the new hash function (compute_dhash)
from src.utils import load_image, save_image, text_to_binary, binary_to_text, compute_dhash

from src.core import embed_watermark, extract_watermark
import shutil
import os
import numpy as np
import random
from PIL import Image, ImageFilter

from src.utils import load_image, save_image, text_to_binary, binary_to_text, compute_dhash, calculate_hamming_distance

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
    """Creates a new user"""
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
    Stamps image with Security Checks (Double Spending Prevention).
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return {"error": "User not found. Register first."}

    # 1. Save Temp File
    file_location = f"static/uploads/{file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    original = load_image(file_location)
    
    # --- SECURITY CHECK: PERCEPTUAL HASH ---
    # Calculate the fingerprint of the incoming image
    img_hash = compute_dhash(original)
    
    # --- SECURITY CHECK: FUZZY MATCHING ---
    # We fetch ALL records and compare them one by one.
    # (For a mini project with <1000 images, this is fast enough)
    all_records = db.query(ImageRegistry).all()
    existing_record = None
    
    for record in all_records:
        distance = calculate_hamming_distance(img_hash, record.image_hash)
        # Threshold 10: If hashes differ by less than 10 bits, it's the SAME image.
        # This accounts for the noise added by the watermark.
        if distance < 10:
            existing_record = record
            break
            
    if existing_record:
        # If the owner is someone else, BLOCK IT
        if existing_record.owner != username:
            return {
                "status": "error", 
                "error": f"COPYRIGHT CONFLICT: This image is already registered to '{existing_record.owner}'."
            }
    # ---------------------------------------------------------------------------

    # 2. Embed Watermark (Using Alpha 40 for Y-Channel)
    watermark_text = f"User:{username}"
    watermarked_img, key_coeffs = embed_watermark(original, watermark_text, alpha=40, username=username)    
    
    # 3. Save Key to User
    user.secret_key_data = key_coeffs
    
    # 4. Register the Hash (Claim Ownership)
    if not existing_record:
        new_record = ImageRegistry(image_hash=img_hash, owner=username)
        db.add(new_record)
        
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
    
    # 3. Decode (Alpha must match stamp function! Using 40 for Y-Channel)
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
        arr = np.array(img)
        noise = np.random.normal(0, 25, arr.shape)
        arr = arr + noise
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
        
    # 2. ATTACK: BLUR (Realistic Focus Blur)
    elif attack_type == "blur":
        img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
        
    # 3. ATTACK: COMPRESSION (Standard Web Quality)
    elif attack_type == "jpeg":
        temp_jpg = f"static/uploads/temp_attack_{filename}.jpg"
        img.save(temp_jpg, "JPEG", quality=75)
        img = Image.open(temp_jpg).convert("RGB")
        
    attack_filename = f"attacked_{attack_type}_{filename}"
    attack_path = f"static/uploads/{attack_filename}"
    
    img.save(attack_path, "PNG")
    
    return {
        "status": "success", 
        "attack_url": f"/static/uploads/{attack_filename}",
        "filename": attack_filename
    }