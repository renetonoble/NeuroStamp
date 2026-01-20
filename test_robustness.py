from src.utils import load_image, save_image
from src.core import embed_watermark, extract_watermark
from PIL import Image
import numpy as np
import os
import pywt

input_path = "assets/lena.jpg"
secret_msg = "NEURO-123"
alpha = 100 # Increased alpha for robustness testing

def test():
    if not os.path.exists(input_path):
        print(f"❌ Error: {input_path} not found.")
        return

    print(f"--- TESTING DWT-SVD ROBUSTNESS (Msg: {secret_msg}) ---")
    
    # 1. Embed
    original = load_image(input_path)
    watermarked, key = embed_watermark(original, secret_msg, alpha=alpha)
    
    save_image(watermarked, "test_wm.png")
    print("[OK] Watermark Embedded.")

    # 2. Base Extraction
    img_reloaded = load_image("test_wm.png")
    extracted = extract_watermark(img_reloaded, key, alpha, len(secret_msg)*8)
    print(f"[>] Direct Extraction: {repr(extracted)} -> {'PASS' if extracted == secret_msg else 'FAIL'}")

    # 3. JPEG Attack (Quality 50)
    img_wm = Image.open("test_wm.png").convert("RGB")
    img_wm.save("test_wm.jpg", "JPEG", quality=50)
    
    # Reload and Extract
    extracted_jpeg = extract_watermark(load_image("test_wm.jpg"), key, alpha, len(secret_msg)*8)
    print(f"[>] JPEG (Q=50) Extraction: {repr(extracted_jpeg)} -> {'PASS' if extracted_jpeg == secret_msg else 'FAIL'}")

    # 4. Noise Attack
    arr = np.array(img_wm).astype(np.float32)
    noise = np.random.normal(0, 15, arr.shape) # Sigma=15 Gaussian Noise
    noisy_arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    save_image(noisy_arr, "test_noise.png")
    
    extracted_noise = extract_watermark(load_image("test_noise.png"), key, alpha, len(secret_msg)*8)
    print(f"[>] Noise (Sigma=15) Extraction: {repr(extracted_noise)} -> {'PASS' if extracted_noise == secret_msg else 'FAIL'}")

if __name__ == "__main__":
    test()
