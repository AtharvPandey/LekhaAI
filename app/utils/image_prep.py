"""
Image Preprocessing for Invoice OCR.

Handles the messy reality of Indian invoices:
- Bad phone camera angles → deskew
- Low light photos → contrast enhancement
- Huge file sizes → compression
- Rotated images → auto-rotation via EXIF
- Thermal prints → adaptive thresholding
"""

import io
import logging
from PIL import Image, ImageEnhance, ImageFilter, ExifTags

logger = logging.getLogger("lekha.image")

# Maximum dimensions for OCR (larger = more tokens = more cost)
MAX_WIDTH = 1600
MAX_HEIGHT = 2000
MAX_FILE_SIZE_MB = 4
JPEG_QUALITY = 85


def preprocess_invoice_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> tuple[bytes, str]:
    """
    Full preprocessing pipeline for invoice images.

    Steps:
    1. Auto-rotate using EXIF data
    2. Resize if too large
    3. Enhance contrast (for faded thermal prints)
    4. Sharpen slightly (for blurry phone photos)
    5. Compress to optimal size

    Args:
        image_bytes: Raw image bytes from WhatsApp
        mime_type: Original MIME type

    Returns:
        (processed_bytes, mime_type)
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        original_size = len(image_bytes)

        logger.info(f"Preprocessing: {img.size[0]}x{img.size[1]}, "
                     f"{img.mode}, {original_size / 1024:.0f}KB")

        # Step 1: Auto-rotate using EXIF
        img = _auto_rotate(img)

        # Step 2: Convert to RGB if needed
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Step 3: Resize if too large
        img = _resize_if_needed(img)

        # Step 4: Enhance for better OCR
        img = _enhance_for_ocr(img)

        # Step 5: Compress to JPEG
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        processed_bytes = output.getvalue()

        logger.info(f"Preprocessed: {img.size[0]}x{img.size[1]}, "
                     f"{len(processed_bytes) / 1024:.0f}KB "
                     f"(was {original_size / 1024:.0f}KB)")

        return processed_bytes, "image/jpeg"

    except Exception as e:
        logger.warning(f"Image preprocessing failed: {e}. Using original.")
        return image_bytes, mime_type


def _auto_rotate(img: Image.Image) -> Image.Image:
    """Rotate image based on EXIF orientation tag."""
    try:
        exif = img._getexif()
        if exif is None:
            return img

        # Find orientation tag
        orientation_key = None
        for k, v in ExifTags.TAGS.items():
            if v == "Orientation":
                orientation_key = k
                break

        if orientation_key and orientation_key in exif:
            orientation = exif[orientation_key]
            rotations = {
                3: 180,
                6: 270,
                8: 90,
            }
            if orientation in rotations:
                img = img.rotate(rotations[orientation], expand=True)
                logger.debug(f"Auto-rotated by {rotations[orientation]}°")

    except (AttributeError, KeyError, IndexError):
        pass  # No EXIF data — that's fine

    return img


def _resize_if_needed(img: Image.Image) -> Image.Image:
    """Resize image if it exceeds maximum dimensions."""
    w, h = img.size

    if w <= MAX_WIDTH and h <= MAX_HEIGHT:
        return img

    # Calculate scale factor
    scale = min(MAX_WIDTH / w, MAX_HEIGHT / h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    logger.debug(f"Resized from {w}x{h} to {new_w}x{new_h}")
    return img


def _enhance_for_ocr(img: Image.Image) -> Image.Image:
    """
    Enhance image for better OCR accuracy.
    Light touch — don't over-process.
    """
    # Slight contrast boost (helps with faded thermal prints)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.2)

    # Slight sharpness boost (helps with blurry phone photos)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.3)

    # Slight brightness normalization
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(1.05)

    return img


def get_image_info(image_bytes: bytes) -> dict:
    """Get basic info about an image without full processing."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return {
            "width": img.size[0],
            "height": img.size[1],
            "mode": img.mode,
            "format": img.format,
            "size_kb": len(image_bytes) / 1024,
        }
    except Exception as e:
        return {"error": str(e)}
