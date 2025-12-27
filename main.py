from fastapi import FastAPI, UploadFile, File, Form, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from src.database import SessionLocal, init_db, User, ImageRegistry
from src.utils import load_image, save_image, binary_to_text, compute_dhash, calculate_hamming_distance
from src.core import embed_watermark, extract_watermark
import shutil, os, uuid, numpy as np
from PIL import Image, ImageFilter

app = FastAPI()

# 1. SECURITY SETUP
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
def get_password_hash(password): return pwd_context.hash(password)
def verify_password(plain, hashed): return pwd_context.verify(plain, hashed)

# 2. APP SETUP
os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
init_db()

# FIX: Proper indentation for database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- AUTH ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    username = request.cookies.get("user_session")
    if not username: return RedirectResponse(url="/")
    return templates.TemplateResponse("index.html", {"request": request, "username": username})

@app.post("/register")
async def register(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        return JSONResponse({"status": "error", "message": "User exists!"})
    new_user = User(username=username, hashed_password=get_password_hash(password), user_uid=str(uuid.uuid4())[:12])
    db.add(new_user); db.commit()
    return JSONResponse({"status": "success", "message": f"ID: {new_user.user_uid}"})

@app.post("/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return JSONResponse({"status": "error", "message": "Invalid Credentials"}, status_code=401)
    resp = JSONResponse({"status": "success"})
    resp.set_cookie(key="user_session", value=username)
    return resp

@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/"); resp.delete_cookie("user_session")
    return resp

# --- CORE ROUTES ---
@app.post("/stamp")
async def stamp_image(username: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user: return {"error": "User error. Relogin."}

    # Save & Load
    path = f"static/uploads/{file.filename}"
    with open(path, "wb") as f: shutil.copyfileobj(file.file, f)
    original = load_image(path)
    
    # Double Spending Check
    img_hash = compute_dhash(original)
    for r in db.query(ImageRegistry).all():
        if calculate_hamming_distance(img_hash, r.image_hash) < 10 and r.owner_uid != user.user_uid:
            owner = db.query(User).filter(User.user_uid == r.owner_uid).first()
            return {"status": "error", "error": f"Conflict: Owned by {owner.username if owner else 'Unknown'}"}

    # Embed
    watermarked, key = embed_watermark(original, f"ID:{user.user_uid}", 40, username)
    user.set_key_data(key)
    
    # Register
    if not db.query(ImageRegistry).filter_by(image_hash=img_hash).first():
        db.add(ImageRegistry(image_hash=img_hash, owner_uid=user.user_uid))
    db.commit()
    
    # Save Output
    out_name = f"stamped_{file.filename}"
    save_image(watermarked, f"static/uploads/{out_name}")
    return {"status": "success", "download_url": f"/static/uploads/{out_name}"}

@app.post("/verify")
async def verify(username: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    key = user.get_key_data() if user else None
    if not key: return {"error": "User/Key not found."}
    
    path = f"static/uploads/verify_{file.filename}"
    with open(path, "wb") as f: shutil.copyfileobj(file.file, f)
    
    # Extract
    text = binary_to_text(extract_watermark(load_image(path), key, 40, len(f"ID:{user.user_uid}")*8, username))
    is_match = (text == f"ID:{user.user_uid}")
    return {"status": "complete", "extracted_text": text, "is_match": is_match, "owner": username if is_match else "Unknown"}

@app.post("/attack")
async def attack(filename: str = Form(...), attack_type: str = Form(...)):
    path = f"static/uploads/{filename}"
    if not os.path.exists(path): return {"error": "File not found"}
    img = Image.open(path).convert("RGB")
    
    if attack_type == "noise":
        arr = np.array(img).astype(np.float32) + np.random.normal(0, 25, (img.size[1], img.size[0], 3))
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    elif attack_type == "blur": img = img.filter(ImageFilter.GaussianBlur(1))
    elif attack_type == "jpeg": 
        img.save(f"{path}.jpg", "JPEG", quality=50); img = Image.open(f"{path}.jpg")
    elif attack_type == "rotate": img = img.rotate(5)
    elif attack_type == "crop": img = img.crop((img.width*0.1, img.height*0.1, img.width*0.9, img.height*0.9))
        
    out = f"attacked_{attack_type}_{filename}"
    img.save(f"static/uploads/{out}")
    return {"status": "success", "attack_url": f"/static/uploads/{out}"}

# --- ADMIN ROUTES (THE BEAUTIFUL VERSION) ---
@app.get("/db-viewer", response_class=HTMLResponse)
async def view_database(request: Request, db: Session = Depends(get_db)):
    users = db.query(User).all()
    registry = db.query(ImageRegistry).all()
    
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>NeuroStamp Database Vault</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
        <style>
            body { background-color: #050505; color: #00ff9d; font-family: 'JetBrains Mono', monospace; }
            .card { background: rgba(20, 20, 20, 0.8); border: 1px solid #333; }
            .card-header { background: #111; border-bottom: 1px solid #333; color: #fff; font-weight: bold; }
            .table { color: #ccc; }
            .table-hover tbody tr:hover { color: #fff; background-color: rgba(0, 255, 157, 0.1); }
            .encrypted { color: #ff0055; word-break: break-all; font-size: 0.85em; }
            .uuid { color: #00d2ff; font-weight: bold; }
            h2 { text-shadow: 0 0 10px rgba(0, 255, 157, 0.5); }
        </style>
    </head>
    <body class="p-5">
        <div class="container">
            <div class="d-flex justify-content-between align-items-center mb-5">
                <h2>📂 ENCRYPTED DATABASE VAULT</h2>
                <a href="/dashboard" class="btn btn-outline-light btn-sm">← BACK TO TERMINAL</a>
            </div>

            <div class="card mb-5 shadow-lg">
                <div class="card-header">🔒 USER CREDENTIALS & KEYS (Table: users)</div>
                <div class="card-body p-0">
                    <table class="table table-dark table-hover mb-0">
                        <thead>
                            <tr class="text-secondary">
                                <th>ID</th>
                                <th>USERNAME</th>
                                <th>PUBLIC UUID</th>
                                <th>PASSWORD HASH (bcrypt)</th>
                                <th>ENCRYPTED WATERMARK KEY (AES-256)</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    for u in users:
        key_preview = str(u.encrypted_key_data)[:40] + "..." if u.encrypted_key_data else "<span class='text-muted'>[NO_KEY_GENERATED]</span>"
        pw_preview = u.hashed_password[:20] + "..." if u.hashed_password else "N/A"

        html_content += f"""
            <tr>
                <td>{u.id}</td>
                <td class="fw-bold text-white">{u.username}</td>
                <td class="uuid">{u.user_uid}</td>
                <td class="text-warning">{pw_preview}</td>
                <td class="encrypted">{key_preview}</td>
            </tr>
        """
        
    html_content += """
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="card shadow-lg">
                <div class="card-header text-info">👁️ COPYRIGHT REGISTRY (Table: image_registry)</div>
                <div class="card-body p-0">
                    <table class="table table-dark table-hover mb-0">
                        <thead>
                            <tr class="text-secondary">
                                <th>ID</th>
                                <th>PERCEPTUAL HASH (dHash)</th>
                                <th>OWNER UUID</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    for r in registry:
        html_content += f"""
            <tr>
                <td>{r.id}</td>
                <td class="text-white"><code>{r.image_hash}</code></td>
                <td class="uuid">{r.owner_uid}</td>
            </tr>
        """
        
    html_content += """
                        </tbody>
                    </table>
                </div>
            </div>
            
            <div class="mt-4 text-center text-muted small">
                SECURE CONNECTION | AES-256 ENCRYPTION ACTIVE
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)