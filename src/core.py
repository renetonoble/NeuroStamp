import pywt
import numpy as np
from PIL import Image
from src.utils import get_scrambled_indices

# --- TRANSFORM LOGIC ---
def apply_dwt(matrix):
    return pywt.dwt2(matrix, 'haar')

def inverse_dwt(coeffs):
    return pywt.idwt2(coeffs, 'haar')

# --- BIPOLAR ROBUST ENGINE (Level 2 + Y-Channel + Sign Detection) ---

def embed_channel(channel_matrix, binary_watermark, alpha=30, secret_key="default"):
    """
    SCIENTIFIC UPGRADE:
    1. Uses Level 2 DWT (Mid-Frequencies).
    2. Uses BIPOLAR embedding (+Alpha for 1, -Alpha for 0).
       This centers the signal around 0, making it robust to fading.
    """
    # 1. Level 1 DWT
    LL1, (LH1, HL1, HH1) = apply_dwt(channel_matrix)
    
    # 2. Level 2 DWT
    LL2, (LH2, HL2, HH2) = apply_dwt(LL1)
    
    # Target: LH2 (Vertical Mid-Frequencies)
    flat_target = LH2.flatten()
    original_coeffs = flat_target.copy()
    
    # --- REDUNDANCY ---
    max_slots = len(flat_target)
    scrambled_indices = get_scrambled_indices(max_slots, secret_key)
    
    start_pos = 100
    available_space = max_slots - start_pos
    num_repeats = available_space // len(binary_watermark)
    if num_repeats < 1: num_repeats = 1
    
    full_message = binary_watermark * num_repeats
    
    # --- BIPOLAR EMBEDDING ---
    for i in range(len(full_message)):
        if start_pos + i >= len(scrambled_indices): break
        target_idx = scrambled_indices[start_pos + i]
        
        # KEY CHANGE: Bipolar Logic
        if full_message[i] == '1':
            flat_target[target_idx] += alpha  # Boost
        else:
            flat_target[target_idx] -= alpha  # Suppress
            
    # Reshape
    modified_LH2 = flat_target.reshape(LH2.shape)
    
    # --- RECONSTRUCTION (With Dimension Fix) ---
    modified_LL1 = inverse_dwt((LL2, (modified_LH2, HL2, HH2)))
    
    # Crop to match Level 1 parent
    h, w = LH1.shape
    modified_LL1 = modified_LL1[:h, :w]
    
    watermarked_channel = inverse_dwt((modified_LL1, (LH1, HL1, HH1)))
    
    return watermarked_channel, original_coeffs

def extract_channel(channel_matrix, key, alpha=30, length=128, secret_key="default"):
    """
    SCIENTIFIC UPGRADE:
    Uses Sign-Based Detection (Threshold = 0).
    Robust against signal fading (Blur/JPEG).
    """
    LL1, (LH1, HL1, HH1) = apply_dwt(channel_matrix)
    LL2, (LH2, HL2, HH2) = apply_dwt(LL1)
    
    flat_target = LH2.flatten()
    
    # --- VOTING ---
    max_slots = len(flat_target)
    scrambled_indices = get_scrambled_indices(max_slots, secret_key)
    
    start_pos = 100
    available_space = max_slots - start_pos
    num_repeats = available_space // length
    if num_repeats < 1: num_repeats = 1
    
    # vote_score[i] will store the sum of differences
    # If sum > 0 -> Bit 1. If sum < 0 -> Bit 0.
    vote_score = [0.0] * length
    
    for r in range(num_repeats):
        for i in range(length):
            abs_idx = start_pos + (r * length) + i
            if abs_idx >= len(scrambled_indices): break
            target_idx = scrambled_indices[abs_idx]
            
            try:
                # Calculate difference
                diff = flat_target[target_idx] - key[target_idx]
                # Accumulate the raw signal (Soft Voting)
                vote_score[i] += diff
            except IndexError:
                pass
                
    # --- DECISION ---
    extracted_bits = ""
    for score in vote_score:
        # KEY CHANGE: Zero Threshold
        # Since we did +Alpha and -Alpha, the average is 0.
        # Anything positive is likely a 1, even if faded.
        if score > 0:
            extracted_bits += "1"
        else:
            extracted_bits += "0"
            
    return extracted_bits

# --- PUBLIC FUNCTIONS (Switched to Y-Channel) ---

def embed_watermark(image_array, watermark_text, alpha=30, username="default"):
    """
    Converts to YCbCr and embeds in Luminance (Y).
    Alpha is lower (30) because Y is more sensitive, but Bipolar logic makes it robust.
    """
    # 1. Convert Array -> Image -> YCbCr
    img_pil = Image.fromarray(image_array.astype('uint8')).convert('YCbCr')
    y, cb, cr = img_pil.split()
    
    y_array = np.array(y)
    
    # 2. Embed in Y Channel
    binary_msg = "".join(format(ord(c), '08b') for c in watermark_text)
    watermarked_y_array, key_coeffs = embed_channel(y_array, binary_msg, alpha, secret_key=username)
    
    # 3. Merge Back
    watermarked_y_array = np.clip(watermarked_y_array, 0, 255).astype('uint8')
    watermarked_y = Image.fromarray(watermarked_y_array)
    
    final_img = Image.merge('YCbCr', (watermarked_y, cb, cr)).convert('RGB')
    
    return np.array(final_img), key_coeffs.tolist()

def extract_watermark(image_array, key, alpha=30, length=None, username="default"):
    """
    Extracts from Y Channel.
    """
    # 1. Convert -> YCbCr -> Extract Y
    img_pil = Image.fromarray(image_array.astype('uint8')).convert('YCbCr')
    y, cb, cr = img_pil.split()
    y_array = np.array(y)
    
    return extract_channel(y_array, key, alpha, length, secret_key=username)