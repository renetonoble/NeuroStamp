# ============================================================
# IMPORTS & INITIALIZATION
# ============================================================
# Load environment variables from .env file early to ensure all
# configuration is available before the app starts
from dotenv import load_dotenv
load_dotenv()

# Core FastAPI imports for web framework, request/response handling
from fastapi import FastAPI, UploadFile, File, Form, Depends, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# SQLAlchemy for database ORM and session management
from sqlalchemy.orm import Session

# Password hashing and cryptography
import bcrypt

# Database models and utilities
from src.database import SessionLocal, init_db, User, ImageRegistry
from src.utils import load_image, save_image, binary_to_text, compute_dhash, calculate_hamming_distance
from src.core import embed_watermark, extract_watermark

# Standard library utilities
import shutil, os, uuid, numpy as np, secrets

# Image processing (PIL/Pillow)
from PIL import Image, ImageFilter

# Secure token signing and validation
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Initialize FastAPI application
app = FastAPI()

# ============================================================
# SECURITY HEADERS MIDDLEWARE
# ============================================================
# Middleware to add security headers to every HTTP response
# Prevents clickjacking, MIME-type sniffing, XSS, and enforces HTTPS
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    # Prevent embedding in iframes (clickjacking protection)
    response.headers["X-Frame-Options"] = "DENY"
    # Prevent browsers from sniffing MIME type (e.g., executing JS in CSS)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Control referrer information sent to external sites
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Disable dangerous browser features (camera, mic, geolocation)
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # Force HTTPS for one year (including subdomains)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Content Security Policy: restrict resource loading to same-origin + CDNs
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://code.jquery.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    return response

# ============================================================
# 1. SECURITY SETUP
# ============================================================
# Configuration for authentication, authorization, and encryption

# Cookie signing secret: prevents cookie tampering
# Loaded from env var or randomly generated for development
APP_SECRET = os.environ.get("NEUROSTAMP_APP_SECRET", secrets.token_hex(32))
COOKIE_SIGNER = URLSafeTimedSerializer(APP_SECRET)

# Admin users list: comma-separated usernames with admin access
# If empty, no admin restrictions applied (for development)
ADMIN_USERS = [u.strip() for u in os.environ.get("NEUROSTAMP_ADMIN_USERS", "").split(",") if u.strip()]

# Secure cookie flag: set to "true" only in production with HTTPS
# When false, cookies sent over HTTP; when true, only sent over HTTPS
SECURE_COOKIES = os.environ.get("NEUROSTAMP_SECURE_COOKIES", "false").lower() == "true"

# Watermark embedding strength (SVD coefficients scaling factor)
# Decision rule: bit is "1" when (S[0]_current - S[0]_original) > WATERMARK_ALPHA/2
# Typical range: 50–80. Higher = more robust but lower PSNR; lower = smaller changes but less robust
# Must match during both embedding and extraction for correct threshold detection
WATERMARK_ALPHA = 70

# Upload size and format restrictions
MAX_UPLOAD_BYTES = int(os.environ.get("NEUROSTAMP_MAX_UPLOAD_MB", "20")) * 1024 * 1024
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
# Prevent decompression bomb attacks (e.g., tiny PNG that expands to huge size)
Image.MAX_IMAGE_PIXELS = 89_000_000  # ~9400x9400 pixels

# ============================================================
# PASSWORD HASHING & VERIFICATION UTILITIES
# ============================================================
# Using bcrypt for secure password storage and verification
# (never store plain-text passwords)

def get_password_hash(password: str) -> str:
    """Hash a plain-text password using bcrypt with salt.
    
    Args:
        password: Plain-text password from user input
    
    Returns:
        bcrypt-hashed password string (safe to store in database)
    """
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash.
    
    Args:
        plain: Plain-text password from login attempt
        hashed: bcrypt hash from database
    
    Returns:
        True if password matches hash; False otherwise
    """
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))


# ============================================================
# 2. SIGNED SESSION HELPERS
# ============================================================
# Session management using cryptographically signed tokens
# Prevents session tampering and forgery attacks

def sign_session(username: str) -> str:
    """Create a cryptographically signed session token.
    
    The token is signed with APP_SECRET, so it cannot be forged
    without knowing the secret. The username is embedded in the token
    and can be recovered by verify_session().
    
    Args:
        username: The authenticated username
    
    Returns:
        URL-safe signed token string (safe to use as cookie value)
    """
    return COOKIE_SIGNER.dumps(username, salt="user-session")

def verify_session(token: str, max_age: int = 86400) -> str | None:
    """Verify and decode a signed session token.
    
    Ensures the token was signed with APP_SECRET and has not expired.
    If valid, returns the embedded username. If invalid/expired, returns None.
    
    Args:
        token: The signed token string (from cookie)
        max_age: Maximum age of token in seconds (default 24 hours)
    
    Returns:
        Username if token is valid; None if tampered or expired
    """
    try:
        return COOKIE_SIGNER.loads(token, salt="user-session", max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

def get_session_user(request: Request) -> str | None:
    """Extract and verify the logged-in username from the request cookies.
    
    Reads the "user_session" cookie, verifies its signature, and returns
    the username if valid. Used to protect routes that require authentication.
    
    Args:
        request: FastAPI request object (contains cookies)
    
    Returns:
        Username if session is valid; None if missing or invalid
    """
    token = request.cookies.get("user_session")
    if not token:
        return None
    return verify_session(token)


# ============================================================
# 3. CSRF PROTECTION (Double-Submit Cookie)
# ============================================================
# CSRF (Cross-Site Request Forgery) prevention using double-submit tokens
# Frontend generates a token, includes it in form submissions;
# backend verifies it matches the token in cookies

def generate_csrf_token() -> str:
    """Generate a new CSRF protection token.
    
    Uses cryptographically random hex string. One token per session.
    Client-side JS reads from cookie and must include in form submissions.
    
    Returns:
        Random 64-character hex string (32 bytes)
    """
    return secrets.token_hex(32)

def validate_csrf(request: Request, csrf_token: str = Form(None)):
    """Validate CSRF token from form against cookie.
    
    Both the cookie and form field must contain matching tokens.
    Comparison uses constant-time comparison to prevent timing attacks.
    
    Args:
        request: FastAPI request (contains cookies)
        csrf_token: Token from form submission (POST parameter)
    
    Raises:
        HTTPException with status 403 if validation fails
    """
    cookie_token = request.cookies.get("csrf_token")
    # Token must exist in both places and match exactly
    if not cookie_token or not csrf_token or not secrets.compare_digest(cookie_token, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


# ============================================================
# 4. UPLOAD VALIDATION
# ============================================================
# Multi-layer validation for file uploads to prevent attacks
# (malicious files, oversized files, file type bypass)

async def validate_upload(file: UploadFile) -> bytes:
    """Validate an uploaded file before processing.
    
    Performs three security checks:
    1. File extension must be in ALLOWED_EXTENSIONS (allowlist)
    2. File size must not exceed MAX_UPLOAD_BYTES
    3. PIL can actually open the file (detects corrupted/fake images)
    
    Args:
        file: FastAPI UploadFile object
    
    Returns:
        File contents as bytes if all checks pass
    
    Raises:
        HTTPException with status 400 or 413 if validation fails
    """
    # CHECK 1: Verify file extension is allowed
    _, ext = os.path.splitext(file.filename or "")
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    
    # CHECK 2: Read and verify file size
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(contents) // (1024*1024)}MB). Max: {MAX_UPLOAD_BYTES // (1024*1024)}MB"
        )
    
    # CHECK 3: Verify the file is a valid, uncorrupted image
    # PIL will raise exception if file is not a valid image or is corrupted
    import io
    try:
        img = Image.open(io.BytesIO(contents))
        img.verify()  # verify() scans for file corruption
    except Exception:
        raise HTTPException(status_code=400, detail="File is not a valid image or is corrupted")
    
    # Reset file pointer for downstream consumers to read from beginning
    await file.seek(0)
    return contents


# ============================================================
# 5. APP SETUP & INITIALIZATION
# ============================================================
# Set up static files, template engine, and database

# Create uploads directory if it doesn't exist (for storing watermarked images)
os.makedirs("static/uploads", exist_ok=True)

# Mount static file directory (CSS, JS, images) at /static path
app.mount("/static", StaticFiles(directory="static"), name="static")

# Set up Jinja2 template engine (for HTML rendering)
templates = Jinja2Templates(directory="templates")
# Disable Jinja2's template caching to avoid TypeError on some platforms
# (some combinations of Jinja2/Starlette pass unhashable types as cache keys)
# Setting cache_size=0 forces re-parsing templates on every request (safe for development)
templates.env.cache_size = 0

# Initialize database (create tables if they don't exist)
init_db()

# Debug: print which DATABASE_URL the app is using at startup
# Helps diagnose database configuration issues in logs
from src.database import DATABASE_URL as _DB_URL
print(f"DEBUG - Using DATABASE_URL={_DB_URL}")

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_secure_filename(filename: str) -> str:
    """Generate a secure filename for uploaded files.
    
    Prevents directory traversal attacks (e.g., "../etc/passwd")
    by using a UUID prefix and sanitizing the original filename.
    
    Args:
        filename: Original filename from upload
    
    Returns:
        Secure filename like "a1b2c3d4_original_image.jpg"
    """
    base = os.path.basename(filename)  # Remove any path separators
    # Keep only safe characters (alphanumeric, dots, dashes, underscores)
    clean_base = "".join(c for c in base if c.isalnum() or c in "._-")
    # Prepend random UUID to make filename unique and prevent collisions
    return f"{uuid.uuid4().hex[:8]}_{clean_base}"

def get_db():
    """FastAPI dependency: Get a database session.
    
    Creates a new SQLAlchemy session for this request and ensures
    it's properly closed after the request completes (even if an error occurs).
    
    Yields:
        SQLAlchemy Session object (used by route handlers via Depends(get_db))
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============================================================
# COOKIE MANAGEMENT HELPER
# ============================================================

def set_secure_cookie(response: Response, key: str, value: str, httponly: bool = True):
    """Set an HTTP cookie with proper security flags.
    
    Applies industry best practices for cookie security:
    - httponly: Prevents JS from reading cookie (default True)
    - samesite: Prevents CSRF attacks (always "Lax")
    - secure: Only sent over HTTPS in production (depends on SECURE_COOKIES setting)
    - max_age: Cookie expires after 24 hours
    
    Args:
        response: FastAPI Response object to modify
        key: Cookie name (e.g., "user_session")
        value: Cookie value (should be signed/encrypted)
        httponly: If True, JS cannot access cookie (default True for security)
    """
    response.set_cookie(
        key=key,
        value=value,
        httponly=httponly,
        samesite="Lax",  # Allows same-site requests with top-level navigation
        secure=SECURE_COOKIES,  # Only HTTPS in production
        max_age=86400,  # 24 hours
    )


# ============================================================
# AUTHENTICATION ROUTES
# ============================================================
# Public routes for user login, registration, and logout

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the login/registration page.
    
    This is the public entry point. No authentication required.
    Redirects to /dashboard if user is already logged in (client-side)
    """
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard (watermarking interface).
    
    Requires valid session cookie. Redirects to login if not authenticated.
    Generates a new CSRF token for form submissions.
    """
    username = get_session_user(request)
    if not username:
        return RedirectResponse(url="/")
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("index.html", {
        "request": request,
        "username": username,
        "csrf_token": csrf_token,
    })
    # Set CSRF cookie (non-httponly so client-side JS can read it)
    set_secure_cookie(response, "csrf_token", csrf_token, httponly=False)
    return response

@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Register a new user account.
    
    Args:
        username: Desired username (must be unique)
        password: Plain-text password (will be hashed)
        db: Database session (injected by FastAPI)
    
    Returns:
        JSON with status and new user's unique ID (user_uid)
        or error message if user already exists or DB error occurs
    """
    # Check if username already exists
    if db.query(User).filter(User.username == username).first():
        return JSONResponse({"status": "error", "message": "User exists!"})
    
    # Create new user with hashed password and unique user_uid
    new_user = User(
        username=username,
        hashed_password=get_password_hash(password),
        user_uid=str(uuid.uuid4())[:12]  # Short unique ID for watermark embedding
    )
    db.add(new_user)
    
    # Commit to database with error handling
    try:
        db.commit()
    except Exception as e:
        db.rollback()  # Rollback on any DB error
        print(f"DB Error during register for user {username}: {e}")
        return JSONResponse(
            {"status": "error", "message": "Database error during registration."},
            status_code=500
        )
    
    return JSONResponse({"status": "success", "message": f"ID: {new_user.user_uid}"})

@app.post("/login")
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Authenticate user and create session cookie.
    
    Args:
        username: Username to authenticate
        password: Plain-text password to verify
        response: FastAPI Response (will set cookie)
        db: Database session (injected by FastAPI)
    
    Returns:
        JSON with {"status": "success"} and user_session cookie set
        or error message if credentials invalid
    """
    # Look up user in database
    user = db.query(User).filter(User.username == username).first()
    
    # Verify user exists and password matches
    if not user or not verify_password(password, user.hashed_password):
        return JSONResponse(
            {"status": "error", "message": "Invalid Credentials"},
            status_code=401
        )
    
    # Create response with success message
    resp = JSONResponse({"status": "success"})
    
    # Set signed session cookie (cannot be forged without APP_SECRET)
    set_secure_cookie(resp, "user_session", sign_session(username))
    
    return resp

@app.get("/logout")
async def logout():
    """Clear session and CSRF cookies, redirect to login.
    
    Doesn't require authentication (safe endpoint).
    """
    resp = RedirectResponse(url="/")
    resp.delete_cookie("user_session")
    resp.delete_cookie("csrf_token")
    return resp


# ============================================================
# CORE ROUTES
# ============================================================
# These routes implement the core watermarking functionality

@app.post("/stamp")
async def stamp_image(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Embed digital watermark into an image.
    
    Workflow:
    1. Authenticate user via session cookie
    2. Validate uploaded image (size, format, corruption)
    3. Check for copyright conflicts (double-spending attack)
    4. Embed watermark using DWT-SVD algorithm
    5. Register image hash in global registry
    6. Return watermarked image for download
    
    Args:
        file: Image file to watermark
        csrf_token: CSRF protection token
        db: Database session
    
    Returns:
        JSON with download URL for watermarked image
    """
    # Derive username from signed session (never trust client-sent username)
    username = get_session_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # CSRF validation (prevents cross-site attacks)
    validate_csrf(request, csrf_token)
    
    # Multi-layer file validation
    file_bytes = await validate_upload(file)
    
    # Look up user in database
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User error. Relogin.")

    # Save uploaded image to temporary location
    secure_name = get_secure_filename(file.filename)
    path = f"static/uploads/{secure_name}"
    with open(path, "wb") as f:
        f.write(file_bytes)
    original = load_image(path)
    
    # ============================================================
    # DOUBLE-SPENDING ATTACK PREVENTION
    # ============================================================
    # Check if this image (or a perceptually similar one) was already watermarked by someone else
    img_hash = compute_dhash(original)
    
    # Fast path: exact hash match
    exact = db.query(ImageRegistry).filter_by(image_hash=img_hash).first()
    if exact and exact.owner_uid != user.user_uid:
        owner = db.query(User).filter(User.user_uid == exact.owner_uid).first()
        return {"status": "error", "error": f"Conflict: Owned by {owner.username if owner else 'Unknown'}"}
    
    # Slow path: bounded perceptual scan (handles similar images, prevents DoS)
    if not exact:
        SCAN_LIMIT = 5000
        for r in db.query(ImageRegistry).limit(SCAN_LIMIT).all():
            if calculate_hamming_distance(img_hash, r.image_hash) < 10 and r.owner_uid != user.user_uid:
                owner = db.query(User).filter(User.user_uid == r.owner_uid).first()
                return {"status": "error", "error": f"Conflict: Owned by {owner.username if owner else 'Unknown'}"}

    # ============================================================
    # WATERMARK EMBEDDING
    # ============================================================
    # Embed user's ID into image using DWT-SVD algorithm
    watermarked, key = embed_watermark(original, f"ID:{user.user_uid}", WATERMARK_ALPHA, username)
    
    # Store the embedding key (encrypted) for later extraction
    user.set_key_data(key)
    
    # ============================================================
    # COPYRIGHT REGISTRY
    # ============================================================
    # Register this image in the global copyright registry (prevent double-stamping)
    if not db.query(ImageRegistry).filter_by(image_hash=img_hash).first():
        h, w, c = original.shape
        db.add(ImageRegistry(
            image_hash=img_hash, 
            owner_uid=user.user_uid,
            original_width=w,
            original_height=h
        ))
    db.commit()
    
    # Save watermarked image and return download URL
    out_name = f"stamped_{secure_name}"
    save_image(watermarked, f"static/uploads/{out_name}")
    return {"status": "success", "download_url": f"/static/uploads/{out_name}"}

@app.post("/verify")
async def verify(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Verify and extract watermark from an image to identify copyright owner.
    
    Workflow:
    1. Validate uploaded image file
    2. Compute perceptual hash (dHash) of the image
    3. Search copyright registry for exact or similar matches (bounded scan)
    4. If found, extract the watermark using the owner's embedding key
    5. Compare extracted ID with expected ID (with tolerance for damage)
    6. Return copyright owner information and match status
    
    This route enables anonymous copyright verification without authentication.
    Allows for robustness against image transformations (crop, resize, JPEG, etc.)
    up to a 22-bit difference in perceptual hash.
    
    Args:
        file: Suspect image file to verify
        db: Database session
    
    Returns:
        JSON with status, extracted text, match result, and owner name
    """
    # ============================================================
    # STEP 1: FILE VALIDATION & LOADING
    # ============================================================
    # Validate uploaded file (extension, size, corruption)
    file_bytes = await validate_upload(file)
    
    # Save suspected image securely with unique filename
    secure_name = get_secure_filename(file.filename)
    path = f"static/uploads/verify_{secure_name}"
    with open(path, "wb") as f:
        f.write(file_bytes)
    
    # Load image as NumPy array for processing
    suspect_img_arr = load_image(path)
    
    # ============================================================
    # STEP 2-3: AUTONOMOUS PIRATE DETECTION (dHash Search)
    # ============================================================
    # Compute perceptual hash (difference hash) of suspected image
    # dHash is robust to scaling, rotation, and compression
    suspect_hash = compute_dhash(suspect_img_arr)
    
    matched_registry = None
    min_dist = 100
    
    # Fast path: check for exact hash match first (most common case)
    exact = db.query(ImageRegistry).filter_by(image_hash=suspect_hash).first()
    if exact:
        matched_registry = exact
        min_dist = 0
    else:
        # Slow path: bounded perceptual scan (prevents DoS, handles similar images)
        # 22-bit tolerance for dHash handles:
        #   - Screenshot/rendering artifacts
        #   - Slight compression variations
        #   - Minor color corrections
        SCAN_LIMIT = 5000  # Only scan first 5000 registered images
        for r in db.query(ImageRegistry).limit(SCAN_LIMIT).all():
            dist = calculate_hamming_distance(suspect_hash, r.image_hash)
            # Update best match if this is closer and within tolerance
            if dist < 22 and dist < min_dist:
                min_dist = dist
                matched_registry = r
            
    if not matched_registry:
        return {
            "status": "error",
            "error": "No matching copyright found in the NeuroStamp Global Registry."
        }
        
    # ============================================================
    # STEP 4: RETRIEVE OWNER & EMBEDDING KEY
    # ============================================================
    # Look up the copyright owner from registry
    user = db.query(User).filter(User.user_uid == matched_registry.owner_uid).first()
    if not user:
        return {"error": "Registry Corrupted: Owner UID missing."}
    
    # Retrieve the owner's encryption key (needed to extract watermark)
    key = user.get_key_data()
    if not key:
        return {"error": "User Key not found."}
    
    # ============================================================
    # STEP 5: RECOVERY ALIGNMENT (Handle Image Transformations)
    # ============================================================
    # If the suspected image was resized/cropped/compressed,
    # restore it to the original dimensions for correct DWT extraction.
    # DWT is sensitive to image dimensions, so alignment is critical.
    orig_w = matched_registry.original_width
    orig_h = matched_registry.original_height
    
    h, w, c = suspect_img_arr.shape
    if orig_w > 0 and orig_h > 0 and (w != orig_w or h != orig_h):
        print(f"   [ALIGNMENT] Restoring dimensions from {w}x{h} back to {orig_w}x{orig_h}")
        pil_img = Image.fromarray(suspect_img_arr)
        # Use LANCZOS resampling (high-quality) to minimize watermark damage
        pil_img = pil_img.resize((orig_w, orig_h), Image.Resampling.LANCZOS)
        suspect_img_arr = np.array(pil_img)
    
    # ============================================================
    # STEP 6: WATERMARK EXTRACTION
    # ============================================================
    # Extract the 120-bit watermark signature using DWT-SVD
    # WATERMARK_ALPHA must match the value used during embedding
    # for correct threshold detection during extraction
    text = extract_watermark(
        suspect_img_arr,
        key,
        WATERMARK_ALPHA,  # Embedding strength threshold
        120,              # Watermark bit length
        user.username
    )
    print(f"DEBUG - Expected: 'ID:{user.user_uid}'")
    print(f"DEBUG - Extracted: '{text}'")
    
    # ============================================================
    # STEP 7: SIGNATURE INTEGRITY VALIDATION
    # ============================================================
    # Validate extracted watermark against expected owner ID
    # Allow up to 32 bits of error to handle:
    #   - Screenshot pipeline artifacts
    #   - JPG compression losses
    #   - Screen rendering variations
    expected = f"ID:{user.user_uid}"
    
    from src.utils import text_to_binary
    
    is_match = False
    if text == expected:
        # Perfect match (no corruption)
        is_match = True
    else:
        # Partial match: convert both to binary and count differing bits
        bin_extracted = text_to_binary(text)
        bin_expected = text_to_binary(expected)
        
        # Pad shorter binary string with zeros for fair comparison
        bin_extracted = bin_extracted.ljust(len(bin_expected), '0')[:len(bin_expected)]
        
        # Count bit errors (Hamming distance at bit level)
        diff_bits = sum(1 for a, b in zip(bin_extracted, bin_expected) if a != b)
        print(f"DEBUG - Bits different: {diff_bits}")
        
        # Accept match if bit error rate is below tolerance (32 bits out of 120)
        if diff_bits <= 32:
            is_match = True
    
    # Return copyright verification result
    # Note: If match found, return corrected ID from database (more reliable than extraction)
    return {
        "status": "complete",
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
    """Simulate various attacks on a watermarked image for robustness testing.
    
    This route is for educational and demonstration purposes. It applies
    common image transformations that might be used to remove or damage watermarks:
    - noise: Add Gaussian noise
    - blur: Apply Gaussian blur
    - jpeg: Compress as JPEG (quality 50%)
    - rotate: Rotate 5 degrees
    - crop: Crop 5% from each edge
    - scale: Downscale by 50%
    
    After applying the attack, users can verify the watermark survives.
    This demonstrates watermark robustness against real-world transformations.
    
    Args:
        filename: Filename of uploaded image (from static/uploads)
        attack_type: Type of attack to simulate
        csrf_token: CSRF protection token
    
    Returns:
        JSON with attacked image URL for download and verification
    """
    # Require authenticated session (prevent anonymous attacks)
    username = get_session_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # CSRF validation (prevents cross-site attacks)
    validate_csrf(request, csrf_token)
    
    # ============================================================
    # ATTACK SIMULATION SETUP
    # ============================================================
    # Sanitize filename to prevent directory traversal (../etc/passwd)
    safe_filename = os.path.basename(filename)
    path = f"static/uploads/{safe_filename}"

    # Verify file exists before processing
    if not os.path.exists(path):
        return {"error": "File not found"}
    
    # Load image in RGB mode (ensures consistent color format)
    img = Image.open(path).convert("RGB")
    
    # ============================================================
    # APPLY SELECTED ATTACK
    # ============================================================
    if attack_type == "noise":
        # Add random Gaussian noise to each pixel
        # Simulates: photography noise, compression artifacts
        arr = np.array(img).astype(np.float32)
        noise = np.random.normal(0, 10, arr.shape)  # Mean=0, StdDev=10
        arr = arr + noise
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        
    elif attack_type == "blur":
        # Apply Gaussian blur (low-pass filter)
        # Simulates: anti-aliasing, soft focus, screen rendering
        img = img.filter(ImageFilter.GaussianBlur(1))
        
    elif attack_type == "jpeg":
        # Compress as JPEG with quality=50 (lossy compression)
        # Simulates: social media upload, messaging app, low-bandwidth scenarios
        img.save(f"{path}.jpg", "JPEG", quality=50)
        img = Image.open(f"{path}.jpg")
        
    elif attack_type == "rotate":
        # Rotate image by 5 degrees
        # Simulates: phone orientation, incorrect scanning, screenshot rotation
        img = img.rotate(5)
        
    elif attack_type == "crop":
        # Crop 5% from each edge (removes ~18% of pixel data)
        # Simulates: manual cropping, framing, content cropping
        img = img.crop((
            img.width*0.05,
            img.height*0.05,
            img.width*0.95,
            img.height*0.95
        ))
        
    elif attack_type == "scale":
        # Downscale to 50% then upscale back (introduces resampling artifacts)
        # Simulates: thumbnail generation, screen resizing, aspect ratio changes
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.Resampling.LANCZOS)
    
    # ============================================================
    # SAVE & RETURN ATTACKED IMAGE
    # ============================================================
    # Save attacked image with descriptive filename
    out = f"attacked_{attack_type}_{safe_filename}"
    img.save(f"static/uploads/{out}")
    
    return {
        "status": "success",
        "attack_url": f"/static/uploads/{out}"
    }


# ============================================================
# ADMIN ROUTES — Requires authenticated session
# ============================================================
# Protected routes for database inspection (admin only)

@app.get("/db-viewer", response_class=HTMLResponse)
async def view_database(request: Request, db: Session = Depends(get_db)):
    """Display the NeuroStamp database in a styled HTML table.
    
    Shows:
    - All registered users and their unique IDs
    - Copyright registry (all watermarked images and owners)
    
    Authentication: Requires valid session cookie
    Authorization: Requires username in NEUROSTAMP_ADMIN_USERS env var
    (if ADMIN_USERS is empty, no admin access is allowed)
    
    Security notes:
    - Does NOT display password hashes or encrypted keys (for safety)
    - Only shows whether user has a watermark key stored
    - Styled with dark theme for security context
    
    Args:
        request: FastAPI request (checked for session cookie)
        db: Database session
    
    Returns:
        HTML page with two tables (users and copyright registry)
    """
    # ============================================================
    # AUTHENTICATION & AUTHORIZATION
    # ============================================================
    # Extract and verify session token
    username = get_session_user(request)
    if not username:
        return RedirectResponse(url="/")
    
    # Check admin authorization: must be in ADMIN_USERS list
    # If ADMIN_USERS is empty, no one gets admin access (secure default)
    if not ADMIN_USERS or username not in ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # ============================================================
    # FETCH DATABASE DATA
    # ============================================================
    # Query all users and copyright registry entries
    users = db.query(User).all()
    registry = db.query(ImageRegistry).all()
    
    # ============================================================
    # BUILD HTML RESPONSE
    # ============================================================
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>NeuroStamp Database Vault</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
        <style>
            /* Dark theme for security context */
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
    
    # Populate users table
    for u in users:
        # SECURITY: Do NOT expose password hashes or encrypted key data
        # Only show whether key exists (boolean flag)
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
    
    # Populate copyright registry table
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
# VISUALIZATION ENGINE ENDPOINTS
# ============================================================
# Routes for visualizing DWT, SVD, and watermark difference maps

@app.get("/visualize", response_class=HTMLResponse)
async def visualize_page(request: Request):
    """Serve the visualization engine page.
    
    This page allows users to upload an image and see detailed visualizations
    of the DWT (Discrete Wavelet Transform), SVD (Singular Value Decomposition),
    and difference maps showing how the watermark modifies the image.
    
    Educational tool for understanding the watermarking algorithm.
    
    Requirements: Authenticated session (checks for user_session cookie)
    
    Args:
        request: FastAPI request
    
    Returns:
        HTML page for visualization interface
    """
    # Require authentication
    username = get_session_user(request)
    if not username:
        return RedirectResponse(url="/")
    
    # Generate new CSRF token for form submissions
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("visualize.html", {
        "request": request,
        "username": username,
        "csrf_token": csrf_token,
    })
    # Set CSRF cookie (non-httponly so client JS can read it)
    set_secure_cookie(response, "csrf_token", csrf_token, httponly=False)
    return response

# Import visualization generation functions
from src.visualizer import generate_visualizations, generate_diff_map

@app.post("/process-vis", response_class=HTMLResponse)
async def process_visualization(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Process an image and generate DWT-SVD-Diff visualizations.
    
    Workflow:
    1. Authenticate user and validate CSRF token
    2. Validate uploaded image file
    3. Generate watermarked version using the same algorithm as /stamp
    4. Create visualization assets:
       - DWT (Wavelet decomposition showing frequency bands)
       - Grid (SVD coefficient grid showing where watermark is embedded)
       - SVD (Singular value magnitudes)
    5. Create difference map (original vs watermarked)
    6. Return page with all visualizations embedded
    
    This is an educational tool to show users exactly where and how
    their watermark is embedded in the frequency domain.
    
    Args:
        file: Image file to process
        csrf_token: CSRF protection token
        db: Database session
    
    Returns:
        HTML page with embedded visualization images
    """
    # ============================================================
    # AUTHENTICATION & CSRF VALIDATION
    # ============================================================
    # Derive identity from session (never trust client)
    username = get_session_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Validate CSRF token (prevents cross-site attacks)
    validate_csrf(request, csrf_token)
    
    # ============================================================
    # FILE VALIDATION & LOADING
    # ============================================================
    # Multi-layer file validation
    file_bytes = await validate_upload(file)
    
    # Create visualization directory if it doesn't exist
    os.makedirs("static/vis", exist_ok=True)
    
    # Generate unique ID for this visualization session
    unique_id = uuid.uuid4().hex[:8]
    
    # Save original image with unique ID
    file_path = f"static/vis/demo_original_{unique_id}.jpg"
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    
    # ============================================================
    # WATERMARK GENERATION (using same algorithm as /stamp)
    # ============================================================
    # Load original image
    original = load_image(file_path)
    
    # Look up user in database
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found. Please re-login.")
    
    # Embed watermark (same as /stamp route)
    wm_img, key = embed_watermark(
        original,
        f"ID:{user.user_uid}",
        WATERMARK_ALPHA,
        username
    )
    
    # Save watermarked image
    wm_path = file_path.replace("original", "watermarked")
    save_image(wm_img, wm_path)
    
    # ============================================================
    # VISUALIZATION GENERATION
    # ============================================================
    # Generate DWT, Grid, and SVD visualizations
    # These show the frequency domain where the watermark lives
    vis_assets = generate_visualizations(file_path, "static/vis", unique_id)
    
    # Generate difference map (shows pixel-level changes)
    # Allows user to see exactly which pixels were modified
    vis_diff = generate_diff_map(file_path, wm_path, "static/vis", unique_id)
    
    # ============================================================
    # RETURN RESULTS
    # ============================================================
    # Generate new CSRF token for next request
    new_csrf = generate_csrf_token()
    
    # Return visualization page with all assets embedded
    response = templates.TemplateResponse("visualize.html", {
        "request": request,
        "username": username,
        "csrf_token": new_csrf,
        "processed": True,  # Flag to show results
        "original_url": "/" + file_path,
        "watermarked_url": "/" + wm_path,
        "vis_dwt": vis_assets["dwt"],      # DWT decomposition
        "vis_grid": vis_assets["grid"],    # SVD coefficient grid
        "vis_svd": vis_assets["svd"],      # SVD magnitudes
        "vis_diff": vis_diff               # Difference map
    })
    
    # Set new CSRF cookie
    set_secure_cookie(response, "csrf_token", new_csrf, httponly=False)
    return response