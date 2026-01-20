# 🧠 NeuroStamp: Robust Digital Watermarking

**NeuroStamp** is a secure, cyber-themed digital watermarking platform designed to protect intellectual property using advanced signal processing.

It embeds invisible, robust watermarks into images using **DWT-SVD (Discrete Wavelet Transform + Singular Value Decomposition)**, ensuring ownership can be proven even after the image is compressed, cropped, or attacked with noise.

![NeuroStamp Visualization](static/vis/demo_watermarked_ab25954d.jpg)
*(Example: Visualization Engine Output)*

## ✨ Key Features

### 🛡️ Robust Watermarking Engine
- **Algorithm**: Block-Based DWT-SVD (Level 1 Haar Wavelet).
- **Resilience**: Survives **JPEG Compression (Quality 50+)** and **Gaussian Noise**.
- **Invisible**: Preserves visual quality while locking ownership data into the image's energy matrix ($S[0]$ values).

### 👁️ Visualization Engine (`/visualize`)
A "Demo Mode" that reveals the magic under the hood for evaluators:
- **Interactive Slider**: Compare Original vs. Watermarked images.
- **Scientific Views**: See the DWT sub-bands and SVD energy heatmaps.
- **Noise Analysis**: Visualizes the amplified difference signal.

### 🔐 Secure Database Vault
- **Encryption**: AES-256 encryption for storing watermark keys.
- **Hashing**: bcrypt algorithm for secure password storage.
- **Protection**: Double-spending protection using Perceptual Hashing (dHash) to prevent re-registering existing images.

## 🚀 Installation

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/yourusername/neurostamp.git
    cd neurostamp
    ```

2.  **Create Virtual Environment**
    ```bash
    python -m venv venv
    .\venv\Scripts\activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

## ⚡ Usage

1.  **Start the Server**
    ```bash
    python -m uvicorn main:app --reload
    ```

2.  **Open the Web Interface**
    Go to `http://localhost:8000`

3.  **Workflow**
    - **Register**: Create a secure identity.
    - **Stamp**: Upload an image to lock it with your signature.
    - **Verify**: key-in your username and upload a suspicious image to prove ownership.
    - **Visualize**: Use the Visualization Engine to explore the algorithm.

## 🧪 Testing Robustness

Run the automated test suite to verify algorithm strength:
```bash
python test_robustness.py
```

## 🏗️ Tech Stack
- **Backend**: Python, FastAPI, SQLAlchemy
- **Math**: NumPy, PyWavelets, OpenCV, SciPy
- **Security**: Fernet (Cryptography), bcrypt
- **Frontend**: Bootstrap 5, Jinja2, Vanilla JS

---
*Built for the Future of IP Protection.*
