from PIL import Image, ImageOps, ImageEnhance
import os, shutil
import pytesseract

def _configure():
    p=os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not p:
        for q in [r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe"]:
            if os.path.exists(q): p=q; break
    if p: pytesseract.pytesseract.tesseract_cmd=p

def extract_text(image:Image.Image, sparse:bool=False)->str:
    _configure()
    img=image.convert("L")
    img=ImageOps.autocontrast(img)
    img=ImageEnhance.Sharpness(img).enhance(1.5)
    img=img.resize((img.width*2,img.height*2))
    # Normal screenshots use PSM 3. Email/shared mobile slips need two passes:
    # PSM 3/6 preserve vertical leg order while PSM 11 recovers sparse right-side
    # prices and thresholds. Returning both lets the DraftKings parser merge them.
    if not sparse:
        return pytesseract.image_to_string(img, config="--psm 3")
    primary = pytesseract.image_to_string(img, config="--psm 3")
    sparse_text = pytesseract.image_to_string(img, config="--psm 11")
    return primary + "\n__OCR_SECOND_PASS__\n" + sparse_text
