import pywt
import numpy as np
from PIL import Image

# --- HELPER FUNCTIONS ---

def text_to_binary(text):
    """Convert text string to binary string."""
    return "".join(format(ord(c), '08b') for c in text)

def binary_to_text(binary):
    """Convert binary string to text."""
    # Split into 8-bit chunks
    chars = []
    for i in range(0, len(binary), 8):
        byte = binary[i:i+8]
        if len(byte) < 8: break
        try:
            chars.append(chr(int(byte, 2)))
        except ValueError:
            pass
    return "".join(chars)

# --- DWT-SVD ENGINE (BLOCK BASED) ---

def embed_watermark(image_array, watermark_text, alpha=50, username="default"):
    """
    Embeds watermark using Block-Based DWT-SVD.
    1. DWT Level 1 -> LL Subband.
    2. Divide LL into 4x4 blocks.
    3. For each block: SVD -> Embed bit in S[0].
    """
    # 1. Convert to YCbCr and extract Y
    img_pil = Image.fromarray(image_array.astype('uint8')).convert('YCbCr')
    y, cb, cr = img_pil.split()
    y_array = np.array(y).astype(float)

    # 2. DWT Level 1
    coeffs = pywt.dwt2(y_array, 'haar')
    LL, (LH, HL, HH) = coeffs
    
    # 3. Prepare Blocks
    h, w = LL.shape
    block_size = 4
    # Ensure divisible by block_size
    h = (h // block_size) * block_size
    w = (w // block_size) * block_size
    LL = LL[:h, :w]
    
    # 4. Prepare Watermark
    binary_msg = text_to_binary(watermark_text)
    num_blocks = (h // block_size) * (w // block_size)
    
    if len(binary_msg) > num_blocks:
        print(f"Warning: Truncating message. Capacity: {num_blocks} bits.")
        binary_msg = binary_msg[:num_blocks]
        
    s0_originals = []
    
    msg_idx = 0
    # Iterate Blocks
    for r in range(0, h, block_size):
        for c in range(0, w, block_size):
            if msg_idx >= len(binary_msg): break
            
            block = LL[r:r+block_size, c:c+block_size]
            U, S, Vt = np.linalg.svd(block, full_matrices=False)
            
            # Store original S[0] for extraction key
            s0_originals.append(float(S[0]))
            
            # Embed Bit into S[0]
            bit = int(binary_msg[msg_idx])
            # Rule: If bit=1, add alpha. If bit=0, do nothing (or sub alpha? No, simpler is better).
            # Let's add alpha * bit.
            S[0] = S[0] + (alpha * bit)
            
            # Reconstruct Block
            block_new = np.dot(U, np.dot(np.diag(S), Vt))
            LL[r:r+block_size, c:c+block_size] = block_new
            
            msg_idx += 1
            
    # 5. Inverse DWT
    # Be careful: LL was cropped. We need to match LH, HL, HH size.
    # Usually LH, HL, HH are same size as original LL.
    # If we cropped LL, we must crop others too.
    LH = LH[:h, :w]; HL = HL[:h, :w]; HH = HH[:h, :w]
    
    coeffs_new = (LL, (LH, HL, HH))
    y_watermarked = pywt.idwt2(coeffs_new, 'haar')
    
    # 6. Merge
    y_watermarked = np.clip(y_watermarked, 0, 255).astype('uint8')
    watermarked_y = Image.fromarray(y_watermarked)
    
    # Resize cb, cr if needed (because we cropped LL/LH..)
    # The output image will be slightly smaller if we cropped.
    # DWT output is usually 2x internal dims.
    out_h, out_w = y_watermarked.shape
    cb = cb.resize((out_w, out_h)); cr = cr.resize((out_w, out_h))
    
    final_img = Image.merge('YCbCr', (watermarked_y, cb, cr)).convert('RGB')
    
    return np.array(final_img), s0_originals

def extract_watermark(image_array, key, alpha=50, length=None, username="default"):
    """
    Extracts watermark using Block-Based DWT-SVD and Original S[0] Key.
    """
    # 1. Setup
    img_pil = Image.fromarray(image_array.astype('uint8')).convert('YCbCr')
    y, cb, cr = img_pil.split()
    y_array = np.array(y).astype(float)
    
    # 2. DWT
    coeffs = pywt.dwt2(y_array, 'haar')
    LL, (LH, HL, HH) = coeffs
    
    h, w = LL.shape
    block_size = 4
    h = (h // block_size) * block_size
    w = (w // block_size) * block_size
    
    s0_originals = key # List of floats
    bits = ""
    idx = 0
    
    for r in range(0, h, block_size):
        for c in range(0, w, block_size):
            if idx >= len(s0_originals): break
            
            block = LL[r:r+block_size, c:c+block_size]
            U, S, Vt = np.linalg.svd(block, full_matrices=False)
            
            s_extracted = S[0]
            s_original = s0_originals[idx]
            
            diff = s_extracted - s_original
            
            # Logic: If diff is close to alpha -> 1. If close to 0 -> 0.
            # Threshold = alpha / 2
            if diff > (alpha / 2):
                bits += "1"
            else:
                bits += "0"
            idx += 1
            
    if length:
        bits = bits[:length]
        
    return binary_to_text(bits)
