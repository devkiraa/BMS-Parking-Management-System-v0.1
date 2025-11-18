import pytesseract
from PIL import Image
import re
from ocr_services.base import OcrService

class TesseractOcr(OcrService):
    def detect_text(self, image_path):
        """Detects text from an image using Tesseract and filters for license plates."""
        try:
            image = Image.open(image_path)
            text = pytesseract.image_to_string(image, config='--psm 6')

            # Filter for alphanumeric characters, remove whitespace
            text = re.sub(r'[^A-Z0-9]', '', text.upper())

            # Look for common license plate patterns
            # This is a basic filter and can be improved
            potential_plates = re.findall(r'[A-Z]{2}[0-9]{1,2}[A-Z]{1,2}[0-9]{3,4}', text)
            if potential_plates:
                return potential_plates[0]

            return text

        except Exception as e:
            print(f"Error during Tesseract OCR: {e}")
            return f"OCR Failed: {e}"
