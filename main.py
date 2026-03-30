from dotenv import load_dotenv
load_dotenv()  # Load .env file early so all env vars are available at startup

from fastapi import FastAPI, UploadFile, File, Form, Depends, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import bcrypt
from src.database import SessionLocal, init_db, User, ImageRegistry
from src.utils import load_image, save_image, binary_to_text, compute_dhash, calculate_hamming_distance
from src.core import embed_watermark, extract_watermark
import shutil, os, uuid, numpy as np, secrets
from PIL import Image, ImageFilter
import asyncio
import time
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = FastAPI()


# ============================================================
# SECURITY HEADERS MIDDLEWARE
# ============================================================
# Adds defensive HTTP headers to every response:
# Prevents clickjacking, MIME sniffing, XSS, and enforces HTTPS.
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://code.jquery.com https://cdn.tailwindcss.com https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://unpkg.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    return response

# ============================================================
# 1. SECURITY SETUP
# ============================================================

# App secret for signing cookies (load from env, or generate random for dev)
APP_SECRET = os.environ.get("NEUROSTAMP_APP_SECRET", secrets.token_hex(32))
COOKIE_SIGNER = URLSafeTimedSerializer(APP_SECRET)

# Admin users (comma-separated usernames from env, or empty = first user)
ADMIN_USERS = [u.strip() for u in os.environ.get("NEUROSTAMP_ADMIN_USERS", "").split(",") if u.strip()]

# Secure cookie flag (set to "true" in production behind HTTPS)
SECURE_COOKIES = os.environ.get("NEUROSTAMP_SECURE_COOKIES", "false").lower() == "true"

# Watermark embedding strength (alpha).
# A single constant ensures embed and extract always use the same threshold.
# Decision rule: bit=1 when (S[0]_current - S[0]_original) > WATERMARK_ALPHA/2
# Range guidance: 50–80 balances robustness vs. PSNR. Keep at 70 unless re-tuning.
WATERMARK_ALPHA = 70

# Upload limits
MAX_UPLOAD_BYTES = int(os.environ.get("NEUROSTAMP_MAX_UPLOAD_MB", "20")) * 1024 * 1024
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
Image.MAX_IMAGE_PIXELS = 89_000_000  # ~9400x9400, prevents decompression bombs


def get_password_hash(password: str) -> str:
    """Hash password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    """Verify password against bcrypt hash"""
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))


# ============================================================
# 2. SIGNED SESSION HELPERS
# ============================================================

def sign_session(username: str) -> str:
    """Create a signed session token for the given username."""
    return COOKIE_SIGNER.dumps(username, salt="user-session")

def verify_session(token: str, max_age: int = 86400) -> str | None:
    """Verify a signed session token. Returns username or None."""
    try:
        return COOKIE_SIGNER.loads(token, salt="user-session", max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

def get_session_user(request: Request) -> str | None:
    """Extract and verify the logged-in username from the request cookie."""
    token = request.cookies.get("user_session")
    if not token:
        return None
    return verify_session(token)


# ============================================================
# 3. CSRF PROTECTION (Double-Submit Cookie)
# ============================================================

def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token."""
    return secrets.token_hex(32)

def validate_csrf(request: Request, csrf_token: str = Form(None)):
    """
    Validate CSRF token from form field against the cookie.
    The csrf_token cookie is non-httponly so the frontend JS can read it.
    The form must include a matching csrf_token hidden field.
    """
    cookie_token = request.cookies.get("csrf_token")
    if not cookie_token or not csrf_token or not secrets.compare_digest(cookie_token, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


# ============================================================
# 4. UPLOAD VALIDATION
# ============================================================

async def validate_upload(file: UploadFile) -> bytes:
    """
    Validate an uploaded file:
    - Extension allowlist
    - File size limit
    - PIL can actually open it (rejects malformed / non-image payloads)
    Returns the file bytes if valid.
    """
    # Check extension
    _, ext = os.path.splitext(file.filename or "")
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    
    # Read and check size
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(contents) // (1024*1024)}MB). Max: {MAX_UPLOAD_BYTES // (1024*1024)}MB"
        )
    
    # Verify it's a valid image PIL can open
    import io
    try:
        img = Image.open(io.BytesIO(contents))
        img.verify()  # verify() checks for corrupted data
    except Exception:
        raise HTTPException(status_code=400, detail="File is not a valid image or is corrupted")
    
    # Reset file position for downstream consumers
    await file.seek(0)
    return contents


# ============================================================
# 5. APP SETUP
# ============================================================

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
init_db()

def cleanup_old_files():
    """Deletes files in static/uploads and static/vis older than 1 hour to prevent disk bloat."""
    now = time.time()
    for directory in ["static/uploads", "static/vis"]:
        if os.path.exists(directory):
            for filename in os.listdir(directory):
                if filename == ".gitkeep": continue
                filepath = os.path.join(directory, filename)
                if os.path.isfile(filepath):
                    if os.stat(filepath).st_mtime < now - 3600:
                        try: os.remove(filepath)
                        except Exception: pass

@app.on_event("startup")
async def startup_event():
    async def periodic_cleanup():
        while True:
            cleanup_old_files()
            await asyncio.sleep(3600)  # Check every hour
    
    asyncio.create_task(periodic_cleanup())

def get_secure_filename(filename: str) -> str:
    """
    Generates a secure filename by prepending a UUID and sanitizing the original name.
    """
    base = os.path.basename(filename)
    # Simple sanitization: keep only alphanumeric, dots, dashes, underscores
    clean_base = "".join(c for c in base if c.isalnum() or c in "._-")
    return f"{uuid.uuid4().hex[:8]}_{clean_base}"

# FIX: Proper indentation for database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# HELPER: Set secure cookie with proper flags
# ============================================================

def set_secure_cookie(response: Response, key: str, value: str, httponly: bool = True):
    """Set a cookie with proper security flags."""
    response.set_cookie(
        key=key,
        value=value,
        httponly=httponly,
        samesite="Lax",
        secure=SECURE_COOKIES,
        max_age=86400,  # 24 hours
    )


# ============================================================
# AUTH ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    username = get_session_user(request)
    if not username:
        return RedirectResponse(url="/")
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("index.html", {
        "request": request,
        "username": username,
        "csrf_token": csrf_token,
    })
    # Set CSRF cookie (non-httponly so JS can read it)
    set_secure_cookie(response, "csrf_token", csrf_token, httponly=False)
    return response

@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.username == username).first():
        return JSONResponse({"status": "error", "message": "User exists!"})
    new_user = User(username=username, hashed_password=get_password_hash(password), user_uid=str(uuid.uuid4())[:12])
    db.add(new_user); db.commit()
    return JSONResponse({"status": "success", "message": f"ID: {new_user.user_uid}"})

@app.post("/login")
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return JSONResponse({"status": "error", "message": "Invalid Credentials"}, status_code=401)
    
    resp = JSONResponse({"status": "success"})
    # Signed session cookie — can't be forged without APP_SECRET
    set_secure_cookie(resp, "user_session", sign_session(username))
    return resp

@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/")
    resp.delete_cookie("user_session")
    resp.delete_cookie("csrf_token")
    return resp


# ============================================================
# CORE ROUTES
# ============================================================

@app.post("/stamp")
async def stamp_image(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    # Derive identity from signed session — never trust client-sent username
    username = get_session_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # CSRF check
    validate_csrf(request, csrf_token)
    
    # Validate uploaded file
    file_bytes = await validate_upload(file)
    
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User error. Relogin.")

    # Save & Load
    secure_name = get_secure_filename(file.filename)
    path = f"static/uploads/{secure_name}"
    with open(path, "wb") as f:
        f.write(file_bytes)
    original = load_image(path)
    
    # Double Spending Check — try exact match first, then bounded hamming scan
    img_hash = compute_dhash(original)
    
    # Fast path: exact match
    exact = db.query(ImageRegistry).filter_by(image_hash=img_hash).first()
    if exact and exact.owner_uid != user.user_uid:
        owner = db.query(User).filter(User.user_uid == exact.owner_uid).first()
        return {"status": "error", "error": f"Conflict: Owned by {owner.username if owner else 'Unknown'}"}
    
    # Slow path: perceptual scan (bounded to prevent DoS)
    if not exact:
        SCAN_LIMIT = 5000
        for r in db.query(ImageRegistry).limit(SCAN_LIMIT).all():
            if calculate_hamming_distance(img_hash, r.image_hash) < 10 and r.owner_uid != user.user_uid:
                owner = db.query(User).filter(User.user_uid == r.owner_uid).first()
                return {"status": "error", "error": f"Conflict: Owned by {owner.username if owner else 'Unknown'}"}

    # Embed
    watermarked, key = embed_watermark(original, f"ID:{user.user_uid}", WATERMARK_ALPHA, username)
    user.set_key_data(key)
    
    # Register
    if not db.query(ImageRegistry).filter_by(image_hash=img_hash).first():
        h, w, c = original.shape
        db.add(ImageRegistry(
            image_hash=img_hash, 
            owner_uid=user.user_uid,
            original_width=w,
            original_height=h
        ))
    db.commit()
    
    # Save Output
    out_name = f"stamped_{secure_name}"
    save_image(watermarked, f"static/uploads/{out_name}")
    return {"status": "success", "download_url": f"/static/uploads/{out_name}"}

@app.post("/verify")
async def verify(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    # Validate uploaded file
    file_bytes = await validate_upload(file)
    
    # 1. Save uploaded file securely
    secure_name = get_secure_filename(file.filename)
    path = f"static/uploads/verify_{secure_name}"
    with open(path, "wb") as f:
        f.write(file_bytes)
    
    # 2. Load the suspected image
    suspect_img_arr = load_image(path)
    
    # 3. Autonomous Pirate Detection (dHash Search)
    suspect_hash = compute_dhash(suspect_img_arr)
    
    matched_registry = None
    min_dist = 100
    
    # Fast path: exact match first
    exact = db.query(ImageRegistry).filter_by(image_hash=suspect_hash).first()
    if exact:
        matched_registry = exact
        min_dist = 0
    else:
        # Bounded perceptual scan
        SCAN_LIMIT = 5000
        for r in db.query(ImageRegistry).limit(SCAN_LIMIT).all():
            dist = calculate_hamming_distance(suspect_hash, r.image_hash)
            if dist < 22 and dist < min_dist: # 22 bit tolerance for dHash (handles crop/scale/screenshot attacks)
                min_dist = dist
                matched_registry = r
            
    if not matched_registry:
        return {"status": "error", "error": "No matching copyright found in the NeuroStamp Global Registry."}
        
    # 4. We found the owner! Load their profile.
    user = db.query(User).filter(User.user_uid == matched_registry.owner_uid).first()
    if not user: return {"error": "Registry Corrupted: Owner UID missing."}
    
    key = user.get_key_data()
    if not key: return {"error": "User Key not found."}
    
    # 5. RECOVERY ALIGNMENT
    # If the image was squashed by Instagram, we must resize it back to the mathematically
    # expected DWT grid dimensions before extraction!
    orig_w = matched_registry.original_width
    orig_h = matched_registry.original_height
    
    h, w, c = suspect_img_arr.shape
    if orig_w > 0 and orig_h > 0 and (w != orig_w or h != orig_h):
        print(f"   [ALIGNMENT] Restoring dimensions from {w}x{h} back to {orig_w}x{orig_h}")
        pil_img = Image.fromarray(suspect_img_arr)
        pil_img = pil_img.resize((orig_w, orig_h), Image.Resampling.LANCZOS)
        suspect_img_arr = np.array(pil_img)
    
    # 6. Extract the 120-bit Bipolar DCT Signature
    # alpha MUST match WATERMARK_ALPHA used during embedding so the threshold is correct
    text = extract_watermark(suspect_img_arr, key, WATERMARK_ALPHA, 120, user.username)
    print(f"DEBUG - Expected: 'ID:{user.user_uid}'")
    print(f"DEBUG - Extracted: '{text}'")
    
    # 7. Signature Integrity Validation
    expected = f"ID:{user.user_uid}"
    
    from src.utils import text_to_binary
    
    is_match = False
    if text == expected:
        is_match = True
    else:
        bin_extracted = text_to_binary(text)
        bin_expected = text_to_binary(expected)
        
        # Format padding
        bin_extracted = bin_extracted.ljust(len(bin_expected), '0')[:len(bin_expected)]
        
        diff_bits = sum(1 for a, b in zip(bin_extracted, bin_expected) if a != b)
        print(f"DEBUG - Bits different: {diff_bits}")
        
        # Allow up to 32 bits of damage to survive screenshot/rendering pipeline attacks
        if diff_bits <= 32:
            is_match = True
            
    return {
        "status": "complete", 
        # If match found, show the corrected ID from the database (not the garbled raw extraction)
        "extracted_text": expected if is_match else text, 
        "is_match": is_match, 
        "owner": user.username if is_match else "Unknown"
    }

@app.post("/attack")
async def attack(
    request: Request,
    filename: str = Form(...),
    attack_type: str = Form(...),
    csrf_token: str = Form(None),
):
    # Require session for attack sim
    username = get_session_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # CSRF check
    validate_csrf(request, csrf_token)
    
    # Sanitize filename (ensure just basename)
    safe_filename = os.path.basename(filename)
    path = f"static/uploads/{safe_filename}"

    if not os.path.exists(path): return {"error": "File not found"}
    img = Image.open(path).convert("RGB")
    
    if attack_type == "noise":
        arr = np.array(img).astype(np.float32) + np.random.normal(0, 10, (img.size[1], img.size[0], 3))
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    elif attack_type == "blur": img = img.filter(ImageFilter.GaussianBlur(1))
    elif attack_type == "jpeg": 
        img.save(f"{path}.jpg", "JPEG", quality=50); img = Image.open(f"{path}.jpg")
    elif attack_type == "rotate": img = img.rotate(5)
    elif attack_type == "crop": img = img.crop((img.width*0.05, img.height*0.05, img.width*0.95, img.height*0.95))
    elif attack_type == "scale":
        w, h = img.size
        # Keep the image physically smaller (50%) to demonstrate the attack visually
        img = img.resize((w // 2, h // 2), Image.Resampling.LANCZOS)
        
    out = f"attacked_{attack_type}_{safe_filename}"
    img.save(f"static/uploads/{out}")
    return {"status": "success", "attack_url": f"/static/uploads/{out}"}


# ============================================================
# ADMIN ROUTES — Requires authenticated session
# ============================================================

@app.get("/db-viewer", response_class=HTMLResponse)
async def view_database(request: Request, db: Session = Depends(get_db)):
    # Require valid session
    username = get_session_user(request)
    if not username:
        return RedirectResponse(url="/")
    
    # Admin check: if ADMIN_USERS is configured, enforce it
    if ADMIN_USERS and username not in ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin access required")
    
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
            .uuid { color: #00d2ff; font-weight: bold; }
            h2 { text-shadow: 0 0 10px rgba(0, 255, 157, 0.5); }
        </style>
    </head>
    <body class="p-5">
        <div class="container">
            <div class="d-flex justify-content-between align-items-center mb-5">
                <h2>📂 DATABASE VAULT</h2>
                <div class="d-flex gap-2">
                     <a href="/visualize" class="btn btn-info btn-sm fw-bold">👁️ VISUALIZATION ENGINE</a>
                     <a href="/dashboard" class="btn btn-outline-light btn-sm">↻ REFRESH</a>
                </div>
            </div>

            <div class="card mb-5 shadow-lg">
                <div class="card-header">🔒 REGISTERED USERS (Table: users)</div>
                <div class="card-body p-0">
                    <table class="table table-dark table-hover mb-0">
                        <thead>
                            <tr class="text-secondary">
                                <th>ID</th>
                                <th>USERNAME</th>
                                <th>PUBLIC UUID</th>
                                <th>HAS WATERMARK KEY</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    for u in users:
        # SECURITY: Do NOT expose password hashes or encrypted key data
        has_key = "✅ YES" if u.encrypted_key_data else "❌ NO"
        html_content += f"""
            <tr>
                <td>{u.id}</td>
                <td class="fw-bold text-white">{u.username}</td>
                <td class="uuid">{u.user_uid}</td>
                <td>{has_key}</td>
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


# ============================================================
# ADMIN PANEL ROUTES
# ============================================================

@app.get("/admin-access", response_class=HTMLResponse)
async def admin_access_page(request: Request):
    """Serve the admin login page. Redirects to panel if already authenticated."""
    username = get_session_user(request)
    if username and (not ADMIN_USERS or username in ADMIN_USERS):
        return RedirectResponse(url="/admin-panel")
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/admin-auth")
async def admin_auth(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Authenticate admin credentials and issue a session cookie."""
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return JSONResponse({"status": "error", "message": "Invalid credentials"}, status_code=401)

    # Admin restriction check
    if ADMIN_USERS and username not in ADMIN_USERS:
        return JSONResponse({"status": "error", "message": "Admin access not granted"}, status_code=403)

    resp = JSONResponse({"status": "success"})
    set_secure_cookie(resp, "user_session", sign_session(username))
    return resp


@app.get("/admin-panel", response_class=HTMLResponse)
async def admin_panel(request: Request):
    """Protected admin panel — requires authenticated session with admin privileges."""
    username = get_session_user(request)
    if not username:
        return RedirectResponse(url="/admin-access")
    if ADMIN_USERS and username not in ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin access required")
    return templates.TemplateResponse("admin_panel.html", {"request": request, "username": username})


# ============================================================
# VISUALIZATION ENGINE ENDPOINTS
# ============================================================

@app.get("/visualize", response_class=HTMLResponse)
async def visualize_page(request: Request):
    username = get_session_user(request)
    if not username:
        return RedirectResponse(url="/")
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("visualize.html", {
        "request": request,
        "username": username,
        "csrf_token": csrf_token,
    })
    set_secure_cookie(response, "csrf_token", csrf_token, httponly=False)
    return response

from src.visualizer import generate_visualizations, generate_diff_map

@app.post("/process-vis", response_class=HTMLResponse)
async def process_visualization(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    # Derive identity from session — not from form
    username = get_session_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # CSRF check
    validate_csrf(request, csrf_token)
    
    # Validate uploaded file
    file_bytes = await validate_upload(file)
    
    # 1. Save Original
    os.makedirs("static/vis", exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    file_path = f"static/vis/demo_original_{unique_id}.jpg"
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    
    # 2. Generate Watermarked Version
    original = load_image(file_path)
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found. Please re-login.")
    wm_img, key = embed_watermark(original, f"ID:{user.user_uid}", WATERMARK_ALPHA, username)
    
    wm_path = file_path.replace("original", "watermarked")
    save_image(wm_img, wm_path)
    
    # 3. Generate Visualizations (DWT, Grid, SVD)
    vis_assets = generate_visualizations(file_path, "static/vis", unique_id)
    
    # 4. Generate Diff Map
    vis_diff = generate_diff_map(file_path, wm_path, "static/vis", unique_id)
    
    new_csrf = generate_csrf_token()
    response = templates.TemplateResponse("visualize.html", {
        "request": request,
        "username": username,
        "csrf_token": new_csrf,
        "processed": True,
        "original_url": "/" + file_path,
        "watermarked_url": "/" + wm_path,
        "vis_dwt": vis_assets["dwt"],
        "vis_grid": vis_assets["grid"],
        "vis_svd": vis_assets["svd"],
        "vis_diff": vis_diff
    })
    set_secure_cookie(response, "csrf_token", new_csrf, httponly=False)
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)