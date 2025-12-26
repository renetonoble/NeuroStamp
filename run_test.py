from src.utils import load_image, save_image, binary_to_text
from src.core import embed_watermark, extract_watermark
import json

# Use a COLOR image (Lena or any photo)
input_path = "assets/lena.jpg" 
secret_msg = "User:ID-12345"
alpha_strength = 60 # Slightly stronger for Blue channel

try:
    print("--- PHASE 1: EMBEDDING (RGB) ---")
    original = load_image(input_path)
    print(f"✅ Loaded Image: {original.shape}")
    
    watermarked_img, key = embed_watermark(original, secret_msg, alpha=alpha_strength)
    save_image(watermarked_img, "final_watermarked_color.png")
    
    with open("key.json", "w") as f:
        json.dump(key, f)
    print("✅ Color Image & Key Saved.")

    print("\n--- PHASE 2: EXTRACTION ---")
    suspicious = load_image("final_watermarked_color.png")
    
    with open("key.json", "r") as f:
        original_key = json.load(f)
        
    bits = len(secret_msg) * 8
    recovered_bits = extract_watermark(suspicious, original_key, alpha=alpha_strength, length=bits)
    text = binary_to_text(recovered_bits)
    
    print(f"🎉 Decoded: {text}")
    
    if text == secret_msg:
        print("🏆 SUCCESS: Color Watermarking Operational.")
    else:
        print(f"⚠️ Mismatch: {text}")

except Exception as e:
    print(f"❌ Error: {e}")