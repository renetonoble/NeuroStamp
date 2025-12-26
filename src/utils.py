from PIL import Image
import numpy as np
import random

def load_image(path):
    """
    Loads an image in RGB mode.
    CRITICAL FIX: Trims odd pixels to ensure Even dimensions.
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
    # Create a list of all possible positions
    indices = list(range(length))
    
    # Use the User's Key to seed the random number generator
    if isinstance(seed_key, str):
        # Convert string to integer seed
        seed = sum(ord(c) for c in seed_key)
    else:
        # If it's already a list/number, use as is
        seed = sum(seed_key) if isinstance(seed_key, list) else int(seed_key)
        
    random.seed(seed)
    random.shuffle(indices)
    
    return indices