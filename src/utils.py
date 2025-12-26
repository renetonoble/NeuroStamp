from PIL import Image
import numpy as np
import random

def load_image(path):
    """
    Loads an image in RGB mode.
    Trims odd pixels to ensure Even dimensions for DWT.
    """
    img = Image.open(path).convert('RGB')
    arr = np.array(img)
    
    # Get dimensions
    h, w, c = arr.shape
    
    # Calculate new even dimensions (drop 1 pixel if odd)
    new_h = h if h % 2 == 0 else h - 1
    new_w = w if w % 2 == 0 else w - 1
    
    # Trim the array if needed
    if new_h != h or new_w != w:
        print(f"   ✂️ Trimming image from {h}x{w} to {new_h}x{new_w} (Even dims required)")
        arr = arr[:new_h, :new_w, :]
        
    return arr

def save_image(array, path):
    """
    Saves a numpy array as an image.
    """
    array = np.clip(array, 0, 255).astype(np.uint8)
    img = Image.fromarray(array)
    img.save(path)
    print(f"💾 Image saved to {path}")

def text_to_binary(text):
    return "".join(format(ord(c), '08b') for c in text)

def binary_to_text(binary):
    chars = [binary[i:i+8] for i in range(0, len(binary), 8)]
    text = ""
    for c in chars:
        if len(c) == 8:
            try:
                text += chr(int(c, 2))
            except:
                pass
    return text

def get_scrambled_indices(length, seed_key):
    """
    Generates a list of unique indices from 0 to length-1,
    shuffled deterministically based on the seed_key.
    """
    indices = list(range(length))
    
    if isinstance(seed_key, str):
        seed = sum(ord(c) for c in seed_key)
    else:
        seed = sum(seed_key) if isinstance(seed_key, list) else int(seed_key)
        
    random.seed(seed)
    random.shuffle(indices)
    
    return indices

def compute_dhash(image_array):
    """
    Generates a 'Perceptual Hash' of the image.
    Robust against watermarking, format changes, and slight resizing.
    """
    # 1. Convert to Grayscale PIL Image
    img = Image.fromarray(image_array.astype('uint8')).convert('L')
    
    # 2. Resize to 9x8 (Reviewing 64 differences)
    # We use 9x8 so we can compare adjacent pixels
    img = img.resize((9, 8), Image.Resampling.LANCZOS)
    
    # 3. Compare Pixels
    pixels = list(img.getdata())
    diff_hash = ""
    
    # Iterate rows
    for row in range(8):
        for col in range(8):
            # Compare pixel[x] with pixel[x+1]
            pixel_left = pixels[row * 9 + col]
            pixel_right = pixels[row * 9 + col + 1]
            
            # If left is brighter, bit is 1. Else 0.
            diff_hash += "1" if pixel_left > pixel_right else "0"
            
    # Convert binary string to Hex for storage
    decimal_val = int(diff_hash, 2)
    hex_hash = hex(decimal_val)[2:] # Strip '0x'
    
    return hex_hash

# ... keep existing imports ...

def hex_to_binary(hex_str):
    """Helper to convert Hex string to Binary string"""
    scale = 16 
    num_of_bits = len(hex_str) * 4
    return bin(int(hex_str, scale))[2:].zfill(num_of_bits)

def calculate_hamming_distance(hash1, hash2):
    """
    Counts how many bits are different between two hashes.
    Lower number = More similar images.
    """
    if len(hash1) != len(hash2):
        return 100 # Mismatch length, return high distance

    bin1 = hex_to_binary(hash1)
    bin2 = hex_to_binary(hash2)
    
    diff_count = 0
    for b1, b2 in zip(bin1, bin2):
        if b1 != b2:
            diff_count += 1
            
    return diff_count