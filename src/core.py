import pywt
import numpy as np

# --- TRANSFORM LOGIC ---
def apply_dwt(matrix):
    return pywt.dwt2(matrix, 'haar')

def inverse_dwt(coeffs):
    return pywt.idwt2(coeffs, 'haar')

# --- CORE ENGINE ---

def embed_channel(channel_matrix, binary_watermark, alpha=50):
    """
    Internal function to watermark a single color channel.
    Input is guaranteed to be even dimensions now.
    """
    LL, (LH, HL, HH) = apply_dwt(channel_matrix)
    flat_HL = HL.flatten()
    
    # Store original for key
    original_coeffs = flat_HL.copy()
    
    # Embedding Logic
    offset = 1000 
    for i in range(len(binary_watermark)):
        if i + offset >= len(flat_HL): break
        if binary_watermark[i] == '1':
            flat_HL[i + offset] += alpha
            
    modified_HL = flat_HL.reshape(HL.shape)
    watermarked_channel = inverse_dwt((LL, (LH, modified_HL, HH)))
    
    # NO CROPPING NEEDED anymore
    return watermarked_channel, original_coeffs

def extract_channel(channel_matrix, key, alpha=50, length=128):
    """
    Internal function to read data.
    """
    LL, (LH, HL, HH) = apply_dwt(channel_matrix)
    flat_HL = HL.flatten()
    offset = 1000
    extracted_bits = ""
    threshold = alpha * 0.5
    
    for i in range(length):
        if i + offset >= len(flat_HL): break
        diff = flat_HL[i + offset] - key[i + offset]
        if diff > threshold:
            extracted_bits += "1"
        else:
            extracted_bits += "0"
    return extracted_bits

# --- PUBLIC FUNCTIONS ---

def embed_watermark(image_array, watermark_text, alpha=80):
    """
    Handles Color (RGB) and Grayscale.
    """
    # 1. Check if Color or Grayscale
    if len(image_array.shape) == 3:
        # RGB Mode
        R = image_array[:, :, 0]
        G = image_array[:, :, 1]
        B = image_array[:, :, 2]
        
        # Text to Binary
        binary_msg = "".join(format(ord(c), '08b') for c in watermark_text)
        print(f"   🔹 Injecting {len(binary_msg)} bits into Blue Channel...")
        
        # Embed in Blue
        watermarked_B, key_coeffs = embed_channel(B, binary_msg, alpha)
        
        # Stack Back Together
        watermarked_image = np.dstack((R, G, watermarked_B))
        return watermarked_image, key_coeffs.tolist()
        
    else:
        # Grayscale Mode (Fallback)
        print("   🔹 Grayscale detected.")
        binary_msg = "".join(format(ord(c), '08b') for c in watermark_text)
        watermarked_img, key_coeffs = embed_channel(image_array, binary_msg, alpha)
        return watermarked_img, key_coeffs.tolist()

def extract_watermark(image_array, key, alpha=50, length=None):
    """
    Extracts from Blue channel (if RGB) or Main channel (if Gray).
    """
    if len(image_array.shape) == 3:
        # Get Blue Channel
        target_channel = image_array[:, :, 2]
    else:
        # Get Grayscale Channel
        target_channel = image_array
        
    return extract_channel(target_channel, key, alpha, length)