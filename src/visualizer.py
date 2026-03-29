import numpy as np
import pywt
import cv2
from PIL import Image
import matplotlib
matplotlib.use('Agg')  # Headless backend — must be set before importing pyplot
import matplotlib.pyplot as plt
import io

def generate_visualizations(image_path, output_dir, unique_id="demo"):
    """
    Generates 4 visualization assets from the input image:
    1. DWT Decomposition (4-panel)
    2. Block Grid Overlay
    3. SVD Heatmap
    4. Svd 3d  surface plot
    """
    # Load Image (Grayscale for DWT)
    img_pil = Image.open(image_path).convert('L')
    img = np.array(img_pil)
    
    # Resize to be even
    h, w = img.shape
    h = (h // 2) * 2; w = (w // 2) * 2
    img = img[:h, :w]
    
    # 1. DWT Decomposition
    coeffs = pywt.dwt2(img, 'haar')
    LL, (LH, HL, HH) = coeffs
    
    # Normalize for display (boost visibility of high frequencies)
    def norm(arr):
        arr = np.abs(arr)
        arr = (arr - arr.min()) / (arr.max() - arr.min() + 1e-5) * 255
        return arr.astype(np.uint8)
        
    vis_dwt = np.vstack([
        np.hstack([norm(LL), norm(LH)]),
        np.hstack([norm(HL), norm(HH)])
    ])
    dwt_filename = f"{unique_id}_vis_dwt.jpg"
    dwt_path = f"{output_dir}/{dwt_filename}"
    cv2.imwrite(dwt_path, vis_dwt)
    
    # 2. Block Grid Overlay on LL
    # We use LL from above
    LL_norm = norm(LL)
    LL_color = cv2.cvtColor(LL_norm, cv2.COLOR_GRAY2BGR)
    
    # Draw Grid (4x4 blocks)
    block_size = 4
    h_ll, w_ll = LL.shape
    for y in range(0, h_ll, block_size):
        cv2.line(LL_color, (0, y), (w_ll, y), (0, 255, 0), 1)
    for x in range(0, w_ll, block_size):
        cv2.line(LL_color, (x, 0), (x, h_ll), (0, 255, 0), 1)
        
    grid_filename = f"{unique_id}_vis_grid.jpg"
    grid_path = f"{output_dir}/{grid_filename}"
    cv2.imwrite(grid_path, LL_color)
    
    # 3. SVD Heatmap (S[0] Energy)
    # Calculate energy map
    energy_map = np.zeros_like(LL)
    
    for r in range(0, h_ll, block_size):
        for c in range(0, w_ll, block_size):
            if r+block_size <= h_ll and c+block_size <= w_ll:
                block = LL[r:r+block_size, c:c+block_size]
                try:
                    U, S, Vt = np.linalg.svd(block, full_matrices=False)
                    s0 = S[0]
                    # Fill block with s0 value
                    energy_map[r:r+block_size, c:c+block_size] = s0
                except:
                    pass
                    
    # Normalize heatmap
    heatmap_norm = norm(energy_map)
    heatmap_color = cv2.applyColorMap(heatmap_norm, cv2.COLORMAP_JET)
    
    svd_filename = f"{unique_id}_vis_svd.jpg"
    svd_path = f"{output_dir}/{svd_filename}"
    cv2.imwrite(svd_path, heatmap_color)
    
    return {
        "dwt": f"/static/vis/{dwt_filename}",
        "grid": f"/static/vis/{grid_filename}",
        "svd": f"/static/vis/{svd_filename}"
    }

def generate_diff_map(original_path, watermarked_path, output_dir, unique_id="demo"):
    """
    Generates difference map between Original and Watermarked.
    """
    img1 = np.array(Image.open(original_path).convert('RGB')).astype(float)
    img2 = np.array(Image.open(watermarked_path).convert('RGB')).astype(float)
    
    # Resize to match smallest
    h = min(img1.shape[0], img2.shape[0])
    w = min(img1.shape[1], img2.shape[1])
    img1 = img1[:h, :w]
    img2 = img2[:h, :w]
    
    # Diff
    diff = np.abs(img1 - img2)
    # Amplify
    diff = diff * 50.0 
    diff = np.clip(diff, 0, 255).astype(np.uint8)
    
    diff_filename = f"{unique_id}_vis_diff.jpg"
    diff_path = f"{output_dir}/{diff_filename}" # Absolute path
    cv2.imwrite(diff_path, cv2.cvtColor(diff, cv2.COLOR_RGB2BGR))
    
    return f"/static/vis/{diff_filename}" # Web path
