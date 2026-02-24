import hashlib

import numpy as np
import pywt
from PIL import Image
from scipy.fftpack import dct, idct


# --- HELPER FUNCTIONS ---

def text_to_binary(text):
    """Convert text string to binary string."""
    return "".join(format(ord(c), "08b") for c in text)


def binary_to_text(binary):
    """Convert binary string to text."""
    chars = []
    for i in range(0, len(binary), 8):
        byte = binary[i : i + 8]
        if len(byte) < 8:
            break
        try:
            chars.append(chr(int(byte, 2)))
        except ValueError:
            pass
    return "".join(chars)


def _rgb_to_ycbcr(image_array):
    img_pil = Image.fromarray(image_array.astype("uint8")).convert("YCbCr")
    y, cb, cr = img_pil.split()
    return np.array(y).astype(float), cb, cr


def _compose_rgb_from_y(y_channel, cb, cr):
    y_img = Image.fromarray(np.clip(y_channel, 0, 255).astype("uint8"))
    out_h, out_w = y_channel.shape
    cb = cb.resize((out_w, out_h))
    cr = cr.resize((out_w, out_h))
    return np.array(Image.merge("YCbCr", (y_img, cb, cr)).convert("RGB"))


def _dct2(block):
    return dct(dct(block.T, norm="ortho").T, norm="ortho")


def _idct2(block):
    return idct(idct(block.T, norm="ortho").T, norm="ortho")


def _stable_seed(key, salt=""):
    digest = hashlib.sha256(f"{key}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


# --- BACKEND: DWT-SVD V1 ---

def _embed_dwt_svd_v1(image, payload_bits, key=None, params=None):
    params = params or {}
    alpha = params.get("alpha", 50)
    block_size = params.get("block_size", 4)

    y_array, cb, cr = _rgb_to_ycbcr(image)
    LL, (LH, HL, HH) = pywt.dwt2(y_array, "haar")

    h, w = LL.shape
    h = (h // block_size) * block_size
    w = (w // block_size) * block_size
    LL = LL[:h, :w]

    num_blocks = (h // block_size) * (w // block_size)
    payload_bits = payload_bits[:num_blocks]

    s0_originals = []
    msg_idx = 0
    for r in range(0, h, block_size):
        for c in range(0, w, block_size):
            if msg_idx >= len(payload_bits):
                break

            block = LL[r : r + block_size, c : c + block_size]
            U, S, Vt = np.linalg.svd(block, full_matrices=False)
            s0_originals.append(float(S[0]))
            bit = int(payload_bits[msg_idx])
            S[0] = S[0] + (alpha * bit)
            LL[r : r + block_size, c : c + block_size] = np.dot(U, np.dot(np.diag(S), Vt))
            msg_idx += 1

    LH = LH[:h, :w]
    HL = HL[:h, :w]
    HH = HH[:h, :w]
    y_watermarked = pywt.idwt2((LL, (LH, HL, HH)), "haar")

    wm_image = _compose_rgb_from_y(y_watermarked, cb, cr)
    extraction_key = {
        "backend": "dwt_svd_v1",
        "s0_originals": s0_originals,
        "payload_length": len(payload_bits),
        "params": {"alpha": alpha, "block_size": block_size},
    }
    return wm_image, extraction_key


def _extract_dwt_svd_v1(image, key, params=None):
    params = params or {}
    y_array, _, _ = _rgb_to_ycbcr(image)
    LL, _ = pywt.dwt2(y_array, "haar")

    key_data = key if isinstance(key, dict) else {"s0_originals": key}
    s0_originals = key_data.get("s0_originals", [])
    key_params = key_data.get("params", {})

    alpha = params.get("alpha", key_params.get("alpha", 50))
    block_size = params.get("block_size", key_params.get("block_size", 4))
    payload_length = params.get("payload_length", key_data.get("payload_length", len(s0_originals)))

    h, w = LL.shape
    h = (h // block_size) * block_size
    w = (w // block_size) * block_size

    bits = []
    idx = 0
    for r in range(0, h, block_size):
        for c in range(0, w, block_size):
            if idx >= len(s0_originals) or idx >= payload_length:
                break
            block = LL[r : r + block_size, c : c + block_size]
            _, S, _ = np.linalg.svd(block, full_matrices=False)
            bits.append("1" if (S[0] - s0_originals[idx]) > (alpha / 2) else "0")
            idx += 1

    return "".join(bits)


# --- BACKEND: DWT-DCT V2 ---
MID_BAND_COORDS = [(2, 2), (2, 3), (3, 2), (3, 3), (1, 4), (4, 1)]
COEFF_PAIR = ((2, 3), (3, 2))


def _embed_dwt_dct_v2(image, payload_bits, key=None, params=None):
    params = params or {}
    delta = params.get("delta", 18.0)
    block_size = params.get("block_size", 8)
    subbands = tuple(params.get("subbands", ("LH", "HL")))

    y_array, cb, cr = _rgb_to_ycbcr(image)
    LL, (LH, HL, HH) = pywt.dwt2(y_array, "haar")
    subband_map = {"LH": LH, "HL": HL, "HH": HH}

    seed = _stable_seed(key or "default", "dwt_dct_v2")
    rng = np.random.default_rng(seed)

    blocks = []
    for sb_name in subbands:
        sb = subband_map[sb_name]
        h, w = sb.shape
        h = (h // block_size) * block_size
        w = (w // block_size) * block_size
        for r in range(0, h, block_size):
            for c in range(0, w, block_size):
                blocks.append((sb_name, r, c))

    perm = rng.permutation(len(blocks))
    payload_bits = payload_bits[: len(blocks)]

    for idx, bit in enumerate(payload_bits):
        sb_name, r, c = blocks[perm[idx]]
        sb = subband_map[sb_name]
        block = sb[r : r + block_size, c : c + block_size]
        dct_block = _dct2(block)
        (i1, j1), (i2, j2) = COEFF_PAIR
        c1 = dct_block[i1, j1]
        c2 = dct_block[i2, j2]
        a1, a2 = abs(c1), abs(c2)
        sign1 = 1.0 if c1 >= 0 else -1.0
        sign2 = 1.0 if c2 >= 0 else -1.0

        if int(bit) == 1 and (a1 - a2) < delta:
            shift = (delta - (a1 - a2)) / 2.0 + 0.25
            a1 += shift
            a2 = max(0.0, a2 - shift)
        elif int(bit) == 0 and (a2 - a1) < delta:
            shift = (delta - (a2 - a1)) / 2.0 + 0.25
            a2 += shift
            a1 = max(0.0, a1 - shift)

        dct_block[i1, j1] = sign1 * a1
        dct_block[i2, j2] = sign2 * a2
        sb[r : r + block_size, c : c + block_size] = _idct2(dct_block)

    y_watermarked = pywt.idwt2((LL, (subband_map["LH"], subband_map["HL"], subband_map["HH"])), "haar")
    wm_image = _compose_rgb_from_y(y_watermarked, cb, cr)
    extraction_key = {
        "backend": "dwt_dct_v2",
        "payload_length": len(payload_bits),
        "seed": int(seed),
        "params": {"delta": delta, "block_size": block_size, "subbands": list(subbands)},
    }
    return wm_image, extraction_key


def _extract_dwt_dct_v2(image, key, params=None):
    params = params or {}
    key_data = key if isinstance(key, dict) else {}
    key_params = key_data.get("params", {})

    delta = params.get("delta", key_params.get("delta", 18.0))
    block_size = params.get("block_size", key_params.get("block_size", 8))
    subbands = tuple(params.get("subbands", key_params.get("subbands", ("LH", "HL"))))
    payload_length = params.get("payload_length", key_data.get("payload_length"))

    y_array, _, _ = _rgb_to_ycbcr(image)
    LL, (LH, HL, HH) = pywt.dwt2(y_array, "haar")
    subband_map = {"LH": LH, "HL": HL, "HH": HH}

    rng = np.random.default_rng(int(key_data.get("seed", _stable_seed("default", "dwt_dct_v2"))))
    blocks = []
    for sb_name in subbands:
        sb = subband_map[sb_name]
        h, w = sb.shape
        h = (h // block_size) * block_size
        w = (w // block_size) * block_size
        for r in range(0, h, block_size):
            for c in range(0, w, block_size):
                blocks.append((sb_name, r, c))

    perm = rng.permutation(len(blocks))
    if payload_length is None:
        payload_length = len(blocks)

    bits = []
    for idx in range(min(payload_length, len(blocks))):
        sb_name, r, c = blocks[perm[idx]]
        sb = subband_map[sb_name]
        dct_block = _dct2(sb[r : r + block_size, c : c + block_size])
        (i1, j1), (i2, j2) = COEFF_PAIR
        c1 = abs(dct_block[i1, j1])
        c2 = abs(dct_block[i2, j2])
        bits.append("1" if (c1 - c2) > 0 else "0")

    return "".join(bits)


BACKENDS = {
    "dwt_svd_v1": {"embed": _embed_dwt_svd_v1, "extract": _extract_dwt_svd_v1},
    "dwt_dct_v2": {"embed": _embed_dwt_dct_v2, "extract": _extract_dwt_dct_v2},
}


def embed(image, payload_bits, key, params=None):
    params = params or {}
    backend = params.get("backend", "dwt_svd_v1")
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}")
    return BACKENDS[backend]["embed"](image, payload_bits, key, params)


def extract(image, key, params=None):
    params = params or {}
    backend = params.get("backend")
    if backend is None and isinstance(key, dict):
        backend = key.get("backend")
    backend = backend or "dwt_svd_v1"
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}")
    return BACKENDS[backend]["extract"](image, key, params)


# Backward-compatible wrappers

def embed_watermark(image_array, watermark_text, alpha=50, username="default", backend="dwt_svd_v1", params=None):
    payload_bits = text_to_binary(watermark_text)
    merged_params = {"alpha": alpha, "backend": backend}
    if params:
        merged_params.update(params)
    return embed(image_array, payload_bits, username, merged_params)


def extract_watermark(image_array, key, alpha=50, length=None, username="default", backend=None, params=None):
    merged_params = {"alpha": alpha}
    if backend:
        merged_params["backend"] = backend
    if length is not None:
        merged_params["payload_length"] = length
    if params:
        merged_params.update(params)
    bits = extract(image_array, key, merged_params)
    if length is not None:
        bits = bits[:length]
    return binary_to_text(bits)
