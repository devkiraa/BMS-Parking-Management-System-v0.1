import io
import os
import re
import traceback
from google.cloud import vision
from google.cloud.vision_v1 import AnnotateImageResponse
from google.api_core import exceptions as google_exceptions
from ocr_services.base import OcrService

class GoogleVisionOcr(OcrService):
    def detect_text(self, image_path):
        """Detects text (potential number plate) in an image, handling standard Indian and BH series formats."""
        try:
            v_client = vision.ImageAnnotatorClient()
        except Exception as e:
            print(f"[ERROR] Initializing Vision Client: {e}")
            return f"OCR Failed: Vision Client Init Error - {e}"

        try:
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image file not found: {image_path}")

            with io.open(image_path, 'rb') as f:
                content = f.read()
            image = vision.Image(content=content)
            image_context = vision.ImageContext(language_hints=["en"])
            response = v_client.text_detection(image=image, image_context=image_context)

            if isinstance(response, AnnotateImageResponse) and response.error.message:
                raise google_exceptions.GoogleAPICallError(response.error.message)

            texts = response.text_annotations
            if not texts:
                return ""

            possible_plates = []
            # Iterate through detected text blocks (skip the first, which is the full text)
            for i, text in enumerate(texts):
                if i == 0: continue # Skip the full text block initially

                block_text = text.description.upper() # Convert to uppercase
                compact_raw = re.sub(r'[^A-Z0-9]', '', block_text) # Remove non-alphanumeric
                if not compact_raw: continue # Skip empty blocks

                # Regex for BH series: YY BH NNNN LL(L)
                bh_match = re.search(r'^(\d{2})(BH)(\d{4})([A-Z]{1,2})$', compact_raw)
                if bh_match:
                    year, bh_marker, nums, letters = bh_match.groups()
                    formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                    possible_plates.append(formatted_plate)
                    continue # Found a match, move to next block

                # Regex for Standard series: LL NN L(L) NNNN
                standard_match = re.search(r'^([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})$', compact_raw)
                if standard_match:
                    state, rto, letters, nums = standard_match.groups()
                    rto_padded = rto.rjust(2, '0') # Pad RTO code if single digit
                    nums_padded = nums.rjust(4, '0') # Pad final numbers
                    letters_formatted = letters if letters else 'XX' # Use XX if letters part is missing
                    formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                    possible_plates.append(formatted_plate)
                    continue # Found a match

                # Fallback: If block looks somewhat like a plate (length, mix of letters/numbers)
                if 6 <= len(compact_raw) <= 10 and re.search(r'\d', compact_raw) and re.search(r'[A-Z]', compact_raw):
                    possible_plates.append(compact_raw) # Add the raw compact version

            # Select the best candidate (prefer formatted ones)
            if possible_plates:
                # Prefer plates that were formatted (matched regex with hyphens)
                formatted = [p for p in possible_plates if '-' in p]
                best_plate = formatted[0] if formatted else possible_plates[0] # Take first formatted, else first found
                return best_plate
            else:
                # If no blocks matched, check the full text (texts[0]) as a last resort
                if texts: # Ensure texts[0] exists
                    full_text_raw = texts[0].description.upper()
                    full_compact_raw = re.sub(r'[^A-Z0-9]', '', full_text_raw)

                    # Try matching BH/Standard within the full compact text
                    bh_match = re.search(r'(\d{2})(BH)(\d{4})([A-Z]{1,2})', full_compact_raw) # Search within
                    if bh_match:
                        year, bh_marker, nums, letters = bh_match.groups()
                        formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                        return formatted_plate

                    standard_match = re.search(r'([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})', full_compact_raw) # Search within
                    if standard_match:
                        state, rto, letters, nums = standard_match.groups()
                        rto_padded = rto.rjust(2, '0'); nums_padded = nums.rjust(4, '0')
                        letters_formatted = letters if letters else 'XX'
                        formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                        return formatted_plate

                return "" # Return empty if nothing found

        except google_exceptions.GoogleAPICallError as e:
            print(f"[ERROR] Vision API Call Error: {e}")
            return f"OCR Failed: API Error - {e}"
        except FileNotFoundError as e:
            print(f"[ERROR] {e}")
            return f"OCR Failed: File not found"
        except Exception as e:
            print(f"[ERROR] Error during text detection: {e}")
            traceback.print_exc()
            return f"OCR Failed: {e}"
