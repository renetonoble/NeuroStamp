import pywt
import numpy as np
from src.utils import get_scrambled_indices

# --- TRANSFORM LOGIC ---
def apply_dwt(matrix):
    return pywt.dwt2(matrix, 'haar')

def inverse_dwt(coeffs):
    return pywt.idwt2(coeffs, 'haar')

# --- SECURE CORE ENGINE ---

def embed_channel(channel_matrix, binary_watermark, alpha=80, secret_key="default"):
    """
    SECURE VERSION: Uses Secret Key Permutation.
    """
    LL, (LH, HL, HH) = apply_dwt(channel_matrix)
    flat_HL = HL.flatten()
    
    # Store original for key
    original_coeffs = flat_HL.copy()
    
    # 1. GENERATE SECRET POSITIONS
    # We use the secret_key to shuffle the pixel locations
    max_slots = len(flat_HL)
    scrambled_indices = get_scrambled_indices(max_slots, secret_key)
    
    # 2. EMBED
    # We skip the first 1000 'scrambled' positions to be safe
    start_pos = 1000
    
    for i in range(len(binary_watermark)):
        if start_pos + i >= len(scrambled_indices): break
        
        # Look up the SECRET location
        target_idx = scrambled_indices[start_pos + i]
        
        if binary_watermark[i] == '1':
            flat_HL[target_idx] += alpha
            
    modified_HL = flat_HL.reshape(HL.shape)
    watermarked_channel = inverse_dwt((LL, (LH, modified_HL, HH)))
    
    return watermarked_channel, original_coeffs

def extract_channel(channel_matrix, key, alpha=80, length=128, secret_key="default"):
    """
    SECURE VERSION: Can only extract if you know the Secret Key permutation.
    """
    LL, (LH, HL, HH) = apply_dwt(channel_matrix)
    flat_HL = HL.flatten()
    
    # 1. REGENERATE SECRET POSITIONS
    # This MUST match the embedding logic exactly
    max_slots = len(flat_HL)
    scrambled_indices = get_scrambled_indices(max_slots, secret_key)
    
    start_pos = 1000
    extracted_bits = ""
    threshold = alpha * 0.5
    
    for i in range(length):
        if start_pos + i >= len(scrambled_indices): break
        
        # Look up the SAME secret location
        target_idx = scrambled_indices[start_pos + i]
        
        diff = flat_HL[target_idx] - key[target_idx]
        
        if diff > threshold:
            extracted_bits += "1"
        else:
            extracted_bits += "0"
            
    return extracted_bits

# --- PUBLIC FUNCTIONS (Updated to accept username) ---

def embed_watermark(image_array, watermark_text, alpha=80, username="default"):
    """
    Handles Color (RGB) and Grayscale.
    """
    if len(image_array.shape) == 3:
        R = image_array[:, :, 0]
        G = image_array[:, :, 1]
        B = image_array[:, :, 2]
        
        binary_msg = "".join(format(ord(c), '08b') for c in watermark_text)
        
        # PASS USERNAME AS SECRET KEY
        watermarked_B, key_coeffs = embed_channel(B, binary_msg, alpha, secret_key=username)
        
        watermarked_image = np.dstack((R, G, watermarked_B))
        return watermarked_image, key_coeffs.tolist()
        
    else:
        # Grayscale
        binary_msg = "".join(format(ord(c), '08b') for c in watermark_text)
        watermarked_img, key_coeffs = embed_channel(image_array, binary_msg, alpha, secret_key=username)
        return watermarked_img, key_coeffs.tolist()

def extract_watermark(image_array, key, alpha=80, length=None, username="default"):
    """
    Extracts from Blue channel (if RGB) or Main channel (if Gray).
    """
    if len(image_array.shape) == 3:
        target_channel = image_array[:, :, 2]
    else:
        target_channel = image_array
        
    return extract_channel(target_channel, key, alpha, length, secret_key=username)