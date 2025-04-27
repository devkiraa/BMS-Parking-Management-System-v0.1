import os
import io
import traceback
import uuid
import re
import csv
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from PIL import Image, ImageTk, UnidentifiedImageError
import cv2
from datetime import datetime, timedelta # Ensure timedelta is imported
import math # Import math for ceil
from pymongo import MongoClient
from bson import ObjectId
from google.cloud import vision
import sys
import pymongo
import time

# ---- CONFIG ----
SERVICE_ACCOUNT_PATH = "service_account.json"
ASSETS_DIR = "assets"

# Check service account file before proceeding
if not os.path.exists(SERVICE_ACCOUNT_PATH):
    messagebox.showerror("Configuration Error", f"Service account JSON not found at: {SERVICE_ACCOUNT_PATH}")
    sys.exit()

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_PATH

# MongoDB Connection
MONGODB_URI = (
    "mongodb+srv://apms4bb:memoriesbringback"
    "@caspianbms.erpwt.mongodb.net/caspiandb"
    "?retryWrites=true&w=majority&appName=Caspianbms"
)

client = None # Initialize client to None
db = None
parking_col = None
property_col = None

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000) # Add timeout
    client.admin.command('ismaster') # Check connection
    db = client['caspiandb']
    parking_col  = db['parking']
    property_col = db['property']
    print("MongoDB connection successful.")
except Exception as e:
    messagebox.showerror("Database Error", f"Could not connect to MongoDB:\n{e}")
    sys.exit()

def find_cameras(max_index=5):
    """Finds available camera indexes by attempting to open and read a frame."""
    cams = []
    # Suppress stderr temporarily to avoid OpenCV error messages for non-existent devices
    original_stderr = os.dup(sys.stderr.fileno())
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stderr.fileno())
    os.close(devnull)

    try:
        for i in range(max_index):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                try:
                    ret, frame = cap.read() # Attempt to read a frame
                    if ret and frame is not None:
                        cams.append(i)
                        print(f"Found working camera index: {i}") # Debugging
                    else:
                        print(f"Camera index {i} opened but failed initial read.")
                except Exception as e:
                    print(f"Error reading from camera index {i} during detection: {e}")
                finally:
                    cap.release()
            else:
                pass # Camera not opened is expected for unavailable indexes

    finally:
        # Restore stderr
        os.dup2(original_stderr, sys.stderr.fileno())
        os.close(original_stderr)

    return cams

CAMERA_INDEXES = find_cameras(5)

# --- Google Cloud Vision OCR ---
# --- Google Cloud Vision OCR (Improved Block Iteration) ---
def detect_text(image_path):
    """
    Detects text (potential number plate) in an image by iterating through
    detected text blocks and applying format regex. Handles standard Indian
    and BH series formats.
    """
    try:
        v_client = vision.ImageAnnotatorClient()
    except Exception as e:
        print(f"[ERROR] Initializing Vision Client: {e}")
        return f"OCR Failed: Vision Client Init Error - {e}"

    try:
        if not os.path.exists(image_path):
             raise FileNotFoundError(f"Image file not found at path: {image_path}")

        with io.open(image_path, 'rb') as f:
            content = f.read()
        image = vision.Image(content=content)

        # --- Add Language Hints ---
        # Helps the API prioritize expected characters.
        # 'en' for English characters commonly on Indian plates.
        # Add 'hi' if Devanagari script might appear and needs detection.
        image_context = vision.ImageContext(language_hints=["en"])

        # --- Perform Text Detection ---
        response = v_client.text_detection(image=image, image_context=image_context)
        texts = response.text_annotations # List of TextAnnotation objects

        if response.error.message:
             raise Exception(f"Vision API Error: {response.error.message}")

        if not texts:
            print("[INFO] Vision API found no text.")
            return "" # Return empty if nothing detected

        print(f"[INFO] Vision API returned {len(texts)} text blocks.")
        # texts[0] is the full detected text, texts[1:] are individual blocks/words/lines

        # --- Iterate through detected blocks (starting from index 1) ---
        # Often, individual blocks are cleaner than the combined text[0]
        possible_plates = []
        for i, text in enumerate(texts):
             # Skip the first annotation (index 0) which is the full text block
             if i == 0:
                  print(f"[DEBUG] Full text (texts[0]): '{text.description.replace('\n', ' ')}'")
                  continue # Skip the full text block for individual regex matching

             block_text = text.description.upper()
             print(f"[DEBUG] Checking Block {i}: '{block_text}'")

             # --- Text Processing for the block ---
             # Remove spaces, hyphens, and non-alphanumeric chars for regex matching
             compact_raw = re.sub(r'[^A-Z0-9]', '', block_text)

             if not compact_raw: # Skip empty blocks after cleaning
                 continue

             print(f"[DEBUG] Cleaned Block {i}: '{compact_raw}'")

             # --- Regex Matching on the compact block text ---

             # 1. Check for BH Series Format
             bh_match = re.search(r'^(\d{2})(BH)(\d{4})([A-Z]{1,2})$', compact_raw)
             if bh_match:
                 year, bh_marker, nums, letters = bh_match.groups()
                 formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                 print(f"[INFO] Found BH plate in block {i}: {formatted_plate}")
                 possible_plates.append(formatted_plate)
                 continue # Found BH, check next block for potentially better read

             # 2. Check for Standard Indian Format
             standard_match = re.search(r'^([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})$', compact_raw)
             if standard_match:
                 state, rto, letters, nums = standard_match.groups()
                 rto_padded = rto.rjust(2, '0')
                 nums_padded = nums.rjust(4, '0')
                 letters_formatted = letters if letters else 'XX'
                 formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                 print(f"[INFO] Found Standard plate in block {i}: {formatted_plate}")
                 possible_plates.append(formatted_plate)
                 continue # Found Standard, check next block

             # 3. Fallback Check (Plausible compact text)
             # Check if the *compact* block itself looks like a plate
             if 6 <= len(compact_raw) <= 10 and re.search(r'\d', compact_raw) and re.search(r'[A-Z]', compact_raw):
                 print(f"[INFO] Found potential fallback plate in block {i}: {compact_raw}")
                 possible_plates.append(compact_raw) # Add the compact version as potential fallback


        # --- Select the best candidate ---
        if possible_plates:
             # Prioritize formatted plates over compact fallbacks
             # Prioritize longer plates (less likely to be fragments)
             # Simple approach: return the first formatted plate found, or the first fallback if no formatted ones.
             # More sophisticated: score candidates based on format, length, confidence (if available)
             best_plate = possible_plates[0] # Start with the first found
             print(f"[INFO] Selecting best plate from candidates: {possible_plates} -> {best_plate}")
             return best_plate
        else:
             print("[WARN] No text blocks matched expected plate formats.")
             # Optional: Try regex on the full text block (texts[0]) as a last resort
             if texts:
                  full_text_raw = texts[0].description.upper()
                  full_compact_raw = re.sub(r'[^A-Z0-9]', '', full_text_raw)
                  print(f"[DEBUG] Last resort check on full text: '{full_compact_raw}'")
                  bh_match = re.search(r'(\d{2})(BH)(\d{4})([A-Z]{1,2})', full_compact_raw) # Less strict search
                  if bh_match:
                      year, bh_marker, nums, letters = bh_match.groups()
                      formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                      print(f"[INFO] Found BH plate in full text (last resort): {formatted_plate}")
                      return formatted_plate
                  standard_match = re.search(r'([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})', full_compact_raw) # Less strict search
                  if standard_match:
                      state, rto, letters, nums = standard_match.groups()
                      rto_padded = rto.rjust(2, '0'); nums_padded = nums.rjust(4, '0')
                      letters_formatted = letters if letters else 'XX'
                      formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                      print(f"[INFO] Found Standard plate in full text (last resort): {formatted_plate}")
                      return formatted_plate

             return "" # Truly nothing found

    except vision.exceptions.GoogleCloudError as e:
        print(f"[ERROR] Vision API Error: {e}")
        return f"OCR Failed: API Error - {e}"
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return f"OCR Failed: File not found"
    except Exception as e:
        print(f"[ERROR] Error during text detection or processing: {e}")
        traceback.print_exc()
        return f"OCR Failed: {e}"

# --- Editable Dialog for Plate Correction ---
class EditableDialog(tk.Toplevel):
    def __init__(self, master, img_path, plate, on_confirm, on_retake):
        super().__init__(master)
        self.title("Confirm/Edit Number Plate")
        self.on_confirm, self.on_retake = on_confirm, on_retake
        self.transient(master) # Keep on top of master
        self.grab_set() # Modal behavior
        self.img_path = img_path # Store path for potential deletion

        # --- Image Display ---
        img_loaded = False
        try:
            # Open image using PIL
            img = Image.open(img_path);
            # Resize for display (using LANCZOS for better quality)
            img.thumbnail((400,300), Image.Resampling.LANCZOS)
            # Convert to PhotoImage for Tkinter
            self.photo = ImageTk.PhotoImage(img)
            # Display in a Label
            tk.Label(self, image=self.photo).pack(padx=10,pady=10)
            img_loaded = True
        except FileNotFoundError:
             tk.Label(self, text=f"Error: Image file not found\n{img_path}", fg="red").pack(padx=10,pady=10)
        except UnidentifiedImageError:
             tk.Label(self, text=f"Error: Cannot identify image file\n{img_path}", fg="red").pack(padx=10,pady=10)
        except Exception as e:
             tk.Label(self, text=f"Unexpected error loading image: {e}", fg="red").pack(padx=10,pady=10)
             print(f"Error loading image in dialog: {e}")

        if not img_loaded:
            self.photo = None # Ensure photo attribute exists even if loading fails

        # --- Plate Entry ---
        tk.Label(self, text="Detected/Enter Plate:", font=('Segoe UI',12)).pack(pady=(0,5))
        self.entry = tk.Entry(self, font=('Segoe UI',14), justify='center', width=20)
        # Use the detected plate unless it's an error message
        initial_plate = plate if not (plate.startswith("OCR Failed") or not plate) else ""
        self.entry.insert(0, initial_plate)
        self.entry.pack(pady=(0,10), padx=10)
        self.entry.focus_set() # Set focus to the entry field
        self.entry.selection_range(0, tk.END) # Select existing text

        # --- Buttons ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="✅ Confirm", command=self._confirm, style="Accent.TButton").pack(side="left", padx=10)
        ttk.Button(btn_frame, text="🔄 Retake",  command=self._retake).pack(side="left", padx=10)

        # --- Bindings ---
        self.protocol("WM_DELETE_WINDOW", self._retake) # Treat closing window as retake
        self.bind("<Return>", self._confirm) # Bind Enter key to confirm
        self.bind("<Escape>", self._retake) # Bind Escape key to retake
        # Bind destroy event AFTER the mainloop starts processing it
        self.bind("<Destroy>", self._delete_image_file_on_destroy)

    def _delete_image_file_on_destroy(self, event):
        """Deletes the temporary image file when the dialog widget is destroyed."""
        # Ensure this runs only when the dialog itself is destroyed
        if event.widget == self:
            if self.img_path and os.path.exists(self.img_path):
                try:
                    os.remove(self.img_path)
                    print(f"Deleted temporary image file: {self.img_path}")
                except Exception as e:
                    print(f"Error deleting temporary image file {self.img_path}: {e}")
            else:
                print(f"Temporary image file not found or already deleted: {self.img_path}")


    def _confirm(self, event=None): # Add event=None to handle binding
        """Handles confirmation action."""
        plate = self.entry.get().strip().upper() # Get, clean, and uppercase
        # Basic validation
        if not plate:
            messagebox.showwarning("Input Required","Please enter a number plate.", parent=self)
            return

        # Validate plate format (allow letters, numbers, and hyphens from formatted plates)
        if not re.fullmatch(r'[A-Z0-9\-]+', plate):
             messagebox.showwarning("Invalid Format", "Plate should contain only letters (A-Z), numbers (0-9), and hyphens (-).", parent=self)
             return

        # Optional: Add length check if desired
        # if not (6 <= len(plate) <= 13): # Adjust length based on expected formats
        #     messagebox.showwarning("Invalid Length", "Plate length seems incorrect.", parent=self)
        #     return

        self.on_confirm(plate) # Call the confirmation callback
        self.destroy() # Close the dialog

    def _retake(self, event=None): # Add event=None to handle binding
        """Handles retake action."""
        self.on_retake() # Call the retake callback
        self.destroy() # Close the dialog


# --- Main Application Class ---
class ParkingApp:
    def __init__(self, root):
        self.root = root
        root.title("🚗 Parking Management System")
        # Set a minimum size and allow resizing
        root.minsize(1000, 650)
        root.geometry("1100x700") # Start slightly larger
        root.configure(bg="#f0f0f0")
        self._make_styles()

        # --- Main Structure ---
        self.nav = ttk.Notebook(root)
        self.entry_tab = ttk.Frame(self.nav, padding=10) # Add padding to frames
        self.exit_tab  = ttk.Frame(self.nav, padding=10)
        self.nav.add(self.entry_tab, text="🚙 Entry")
        self.nav.add(self.exit_tab, text="🏁 Exit")
        self.nav.pack(fill="both", expand=True, padx=5, pady=5)

        # Build UI for each tab
        self._build_tab(self.entry_tab, True)
        self._build_tab(self.exit_tab, False)

        # Bind events
        self.nav.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self.root.bind('<Return>', self._on_enter_press) # Bind Enter globally

        # Start camera for the initially selected tab after a short delay
        self.root.after(150, self._trigger_initial_camera_start)

    def _on_enter_press(self, event):
        """Handles the Enter key press to trigger capture on the current tab."""
        # Check if focus is on an Entry widget, if so, do nothing (allow typing)
        focused_widget = self.root.focus_get()
        if isinstance(focused_widget, (tk.Entry, ttk.Entry, scrolledtext.ScrolledText)):
            return # Don't trigger capture if typing in an entry/text box

        # Find the currently selected tab widget
        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return # No tab selected
            current_tab_widget = self.nav.nametowidget(current_tab_name)

            # If the tab widget has a method to trigger capture, call it
            if hasattr(current_tab_widget, 'trigger_capture') and callable(getattr(current_tab_widget, 'trigger_capture')):
                # Check if the capture button is enabled before triggering
                if hasattr(current_tab_widget, '_btn_capture') and current_tab_widget._btn_capture['state'] == tk.NORMAL:
                    current_tab_widget.trigger_capture()
                else:
                    print("Capture button is disabled, Enter key ignored.")
            else:
                print("Current tab widget does not have 'trigger_capture' method.")
        except tk.TclError:
            print("Error getting current tab widget (might be during setup/teardown).")
        except Exception as e:
            print(f"Error during Enter press handling: {e}")


    def _on_tab_change(self, event):
        """Handles tab changes by stopping the old camera and starting the new one."""
        # Stop camera on the previously active tab (if any)
        # Note: This logic might be complex if tabs are added/removed dynamically.
        # It assumes only entry_tab and exit_tab exist.
        # A more robust way might involve tracking the previously selected tab index.
        for tab in (self.entry_tab, self.exit_tab):
             if tab and hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                 # Check if the tab is *not* the newly selected one
                 try:
                     if self.nav.index(self.nav.select()) != self.nav.index(tab): # Compare indices
                         tab.stop_camera()
                 except tk.TclError:
                     # Handle cases where a tab might not be fully realized yet
                     print(f"TCL error comparing tabs during tab change (likely benign).")
                 except Exception as e:
                     print(f"Error stopping camera on non-active tab change: {e}")


        # Start camera on the newly selected tab
        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return
            widget = self.nav.nametowidget(current_tab_name)
            if widget and hasattr(widget,'start_camera') and callable(getattr(widget, 'start_camera')):
                widget.start_camera()
        except tk.TclError:
             print("Error getting current tab widget for starting camera (might be during setup/teardown).")
        except Exception as e:
            print(f"Error starting camera on tab change: {e}")

    def _trigger_initial_camera_start(self):
        """Trigger the start_camera for the initially selected tab."""
        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return
            current_tab_widget = self.nav.nametowidget(current_tab_name)
            if current_tab_widget and hasattr(current_tab_widget, 'start_camera') and callable(getattr(current_tab_widget, 'start_camera')):
                current_tab_widget.start_camera()
        except tk.TclError:
             print("Error getting initial tab widget (might be during setup).")
        except Exception as e:
            print(f"Error starting camera for initial tab: {e}")
            # Try to display error on canvas if it exists
            if hasattr(current_tab_widget, '_canvas'):
                current_tab_widget._canvas.config(text=f"Cam Start Error: {e}", image='')
                current_tab_widget._canvas.imgtk = None # Clear image reference

    def _make_styles(self):
        """Configures ttk styles for a more modern look."""
        s = ttk.Style()
        s.theme_use('clam') # 'clam', 'alt', 'default', 'classic'

        # General widget styling
        s.configure(".", font=('Segoe UI', 10), background="#f0f0f0") # Default font and background
        s.configure("TLabel", background="#f0f0f0", foreground="#333") # Standard labels
        s.configure("TEntry", fieldbackground="white", foreground="#333")
        s.configure("TCombobox", fieldbackground="white", foreground="#333")
        s.map("TCombobox", fieldbackground=[('readonly','white')]) # Ensure readonly bg is white

        # Notebook styling
        s.configure("TNotebook", background="#e1e1e1", borderwidth=0)
        s.configure("TNotebook.Tab", padding=[12, 8], font=('Segoe UI', 11, 'bold'), background="#d0d0d0", foreground="#444")
        s.map("TNotebook.Tab",
              background=[("selected", "#f0f0f0"), ("active", "#e8e8e8")], # Selected tab matches frame bg
              foreground=[("selected", "#0078d4"), ("active", "#333")])

        # Button styling
        s.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=(10, 6),
                    background="#e1e1e1", foreground="#333", borderwidth=1, relief="raised")
        s.map("TButton",
              background=[('active', '#c0c0c0'), ('disabled', '#d9d9d9')],
              foreground=[('disabled', '#a3a3a3')])

        # Special Accent Button (for Confirm/Capture)
        s.configure("Accent.TButton", font=('Segoe UI', 11, 'bold'), background="#0078d4", foreground="white")
        s.map("Accent.TButton",
              background=[('active', '#005a9e'), ('disabled', '#b0b0b0')],
              foreground=[('disabled', '#f0f0f0')])

        # Video Canvas Label Style
        s.configure("VideoCanvas.TLabel", background="black", foreground="white", font=('Segoe UI', 14), anchor="center")

        # Log Text Style
        # ScrolledText is a Tk widget, not Ttk, styling is done directly
        # See _build_tab where log widget is created

    def _build_tab(self, frame, is_entry):
        """Builds the UI elements for a single tab (Entry or Exit)."""
        # Configure grid weights for resizing behavior
        frame.columnconfigure(0, weight=2) # Left side (video + controls) takes more space
        frame.columnconfigure(1, weight=1) # Right side (logs) takes less space
        frame.rowconfigure(0, weight=1)    # Allow vertical expansion

        section = "Entry" if is_entry else "Exit"

        # --- Left Side Frame ---
        left_frame = ttk.Frame(frame, padding=5)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_frame.columnconfigure(0, weight=1) # Make column 0 expandable
        # Configure rows within the left frame
        left_frame.rowconfigure(0, weight=0) # Property row
        left_frame.rowconfigure(1, weight=0) # Slots row
        left_frame.rowconfigure(2, weight=0) # Camera row
        left_frame.rowconfigure(3, weight=1) # Canvas row - Allow vertical expansion
        left_frame.rowconfigure(4, weight=0) # Button row

        # --- Property Selection ---
        prop_frame = ttk.Frame(left_frame)
        prop_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(prop_frame, text="Property:", width=8).pack(side="left", padx=(0, 5))
        prop_var = tk.StringVar()
        try:
            # Fetch names, sorted alphabetically for better usability
            props = list(property_col.find({}, {"name": 1}).sort("name", 1))
            names = [p['name'] for p in props if 'name' in p]
        except Exception as e:
            print(f"Error fetching properties: {e}")
            names = []
            messagebox.showerror("DB Error", f"Could not fetch properties: {e}")

        cbp = ttk.Combobox(prop_frame, textvariable=prop_var, values=names, state="readonly", width=30)
        property_available = bool(names)
        if property_available:
            cbp.current(0)
        else:
            cbp.set("No Properties Found")
            cbp.config(state="disabled")
        cbp.pack(side="left", fill="x", expand=True)

        # --- Available Slots Display ---
        slots_lbl = ttk.Label(left_frame, text="Slots: N/A", font=('Segoe UI', 10))
        slots_lbl.grid(row=1, column=0, sticky="w", pady=2, padx=5)

        def refresh_slots(*args):
            """Refreshes the displayed slot count for the selected property."""
            selected_prop_name = prop_var.get()
            if selected_prop_name and selected_prop_name != "No Properties Found":
                try:
                    # Fetch the specific document for slot info
                    doc = property_col.find_one({"name": selected_prop_name}, {"available_parking_spaces": 1, "parking_spaces": 1})
                    if doc:
                        avail = doc.get('available_parking_spaces', 'N/A')
                        total = doc.get('parking_spaces', 'N/A')
                        slots_lbl.config(text=f"Slots Available: {avail} / {total}")
                    else:
                        slots_lbl.config(text="Slots: Error (Not Found)")
                except pymongo.errors.ConnectionFailure:
                     slots_lbl.config(text="Slots: DB Connection Error")
                     print(f"DB connection error during slots refresh.")
                except Exception as e:
                    slots_lbl.config(text="Slots: Error")
                    print(f"Database error during slots refresh: {e}")
            else:
                slots_lbl.config(text="Slots: N/A")

        prop_var.trace_add('write', refresh_slots) # Update slots when property changes
        if property_available: refresh_slots() # Initial call

        # --- Camera Selection ---
        cam_frame = ttk.Frame(left_frame)
        cam_frame.grid(row=2, column=0, sticky="ew", pady=2)
        ttk.Label(cam_frame, text="Camera:", width=8).pack(side="left", padx=(0, 5))
        cam_var = tk.IntVar()
        cam_values = CAMERA_INDEXES if CAMERA_INDEXES else ["N/A"]
        cam_state = "readonly" if CAMERA_INDEXES else "disabled"

        cbcam = ttk.Combobox(cam_frame, textvariable=cam_var, values=cam_values, state=cam_state, width=5)
        if CAMERA_INDEXES:
            cam_var.set(CAMERA_INDEXES[0]) # Set default if cameras exist
        else:
            cam_var.set(-1) # Or some indicator for no camera
        cbcam.pack(side="left")

        # --- Video Canvas ---
        canvas = ttk.Label(left_frame, text="Initializing camera...", style="VideoCanvas.TLabel")
        canvas.grid(row=3, column=0, sticky="nsew", pady=5, padx=5)

        # --- Capture Button ---
        btn_capture = ttk.Button(left_frame, text="📸 Capture & Process", style="Accent.TButton")
        # Command is set later after trigger_capture is defined

        if not property_available or not CAMERA_INDEXES:
            btn_capture.config(state="disabled")
            if not property_available and not CAMERA_INDEXES:
                 btn_capture.config(text="🚫 Setup Required")
            elif not property_available:
                 btn_capture.config(text="🚫 Add Property First")
            else: # No cameras
                 btn_capture.config(text="🚫 No Camera Found")

        btn_capture.grid(row=4, column=0, pady=10)
        frame._btn_capture = btn_capture # Store reference for Enter key check

        # --- Right Side Frame (Logs) ---
        right_frame = ttk.Frame(frame, padding=5)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(1, weight=1) # Log area expands

        # --- Log Controls (Clear/Export) ---
        log_ctrl_frame = ttk.Frame(right_frame)
        log_ctrl_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        clear_btn = ttk.Button(log_ctrl_frame, text="🗑 Clear Logs", width=12)
        clear_btn.pack(side="left", padx=(0, 5))
        export_btn = ttk.Button(log_ctrl_frame, text="⬇️ Export CSV", width=12)
        export_btn.pack(side="left")

        # --- Log Display ---
        log = scrolledtext.ScrolledText(right_frame, width=45, height=15, # Slightly wider
                                        font=("Consolas", 10), wrap=tk.WORD,
                                        bg="#ffffff", fg="#333333", relief="solid", borderwidth=1,
                                        state=tk.DISABLED) # Start disabled
        log.grid(row=1, column=0, sticky="nsew")

        # --- Log Utility Function ---
        def append_log(message):
            """Appends a message to the log widget, ensuring it's enabled."""
            try:
                log.config(state=tk.NORMAL)
                log.insert(tk.END, message)
                log.see(tk.END) # Scroll to the end
                log.config(state=tk.DISABLED)
            except tk.TclError as e:
                 print(f"Error appending to log (widget might be destroyed): {e}")
            except Exception as e:
                 print(f"Unexpected error appending log: {e}")


        # Set commands for log control buttons
        def clear_log_action():
            log.config(state=tk.NORMAL)
            log.delete('1.0', tk.END)
            # Re-add header after clearing
            log.insert("end", f"📄 {section} Log History:\n" + "="*25 + "\n")
            log.config(state=tk.DISABLED)
        clear_btn.config(command=clear_log_action)
        export_btn.config(command=lambda: self._export(log, section))

        # --- Store references and state on the frame widget itself ---
        frame._state = {'cap': None, 'frame': None, 'after_id': None}
        frame._log_widget = log # Store log widget reference
        frame._append_log = append_log # Store utility function
        frame._canvas = canvas
        frame._prop_var = prop_var # Store prop_var for refresh_slots access
        frame._refresh_slots = refresh_slots # Store function reference

        # --- Camera Handling Functions (defined within _build_tab) ---
        def update_feed():
            """Updates the video feed on the canvas."""
            cap = frame._state.get('cap') # Use get for safety
            if cap is None or not cap.isOpened():
                if frame._state.get('after_id') is not None:
                    frame._canvas.after_cancel(frame._state['after_id'])
                    frame._state['after_id'] = None
                return

            try:
                ok, frm = cap.read()
                if ok and frm is not None:
                    frame._state['frame'] = frm # Store the latest raw frame
                    img_rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB)
                    img_pil = Image.fromarray(img_rgb)
                    canvas_w = frame._canvas.winfo_width()
                    canvas_h = frame._canvas.winfo_height()
                    if canvas_w <= 1 or canvas_h <= 1:
                         frame._state['after_id'] = frame._canvas.after(100, update_feed)
                         return
                    img_pil.thumbnail((canvas_w, canvas_h), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img_pil)
                    frame._canvas.imgtk = photo
                    frame._canvas.config(image=photo, text="")
                else:
                    print(f"Warning: Frame read failed for camera {cam_var.get()}")

            except Exception as e:
                print(f"Error in update_feed for camera {cam_var.get()}: {e}")
                stop_camera()
                frame._canvas.config(image='', text=f"Feed Error: {e}")
                frame._canvas.imgtk = None
                return

            frame._state['after_id'] = frame._canvas.after(40, update_feed) # Approx 25 FPS

        def start_camera(event=None):
            """Starts or restarts the selected camera."""
            stop_camera() # Ensure previous camera is stopped

            selected_cam_index = cam_var.get()
            if selected_cam_index == -1 or not isinstance(selected_cam_index, int):
                 frame._canvas.config(text="No Camera Selected/Available", image='')
                 frame._canvas.imgtk = None
                 btn_capture.config(state="disabled", text="🚫 Select Camera")
                 return

            append_log(f"⏳ Initializing camera {selected_cam_index}...\n")
            frame._canvas.config(text=f"Starting Camera {selected_cam_index}...", image='')
            frame._canvas.imgtk = None
            self.root.update_idletasks()

            cap = cv2.VideoCapture(selected_cam_index)
            time.sleep(0.5)

            if not cap.isOpened():
                messagebox.showerror("Camera Error", f"Cannot open camera index {selected_cam_index}", parent=self.root)
                append_log(f"❌ Failed to open camera {selected_cam_index}\n")
                frame._state['cap'] = None
                frame._canvas.config(text="Failed to Open Camera", image='')
                frame._canvas.imgtk = None
                btn_capture.config(state="disabled", text="🚫 Camera Error")
                return

            # --- Test Read ---
            read_success = False
            try:
                for _ in range(5):
                    ok, test_frame = cap.read()
                    if ok and test_frame is not None:
                        read_success = True
                        break
                    time.sleep(0.1)
                if not read_success:
                    raise IOError("Failed to read initial frames after opening.")
            except Exception as e:
                cap.release()
                frame._state['cap'] = None
                messagebox.showerror("Camera Error", f"Error reading initial frames from camera {selected_cam_index}: {e}", parent=self.root)
                append_log(f"❌ Failed initial read: {e}\n")
                frame._canvas.config(text="Camera Read Error", image='')
                frame._canvas.imgtk = None
                btn_capture.config(state="disabled", text="🚫 Read Error")
                return
            # --- End Test Read ---

            frame._state['cap'] = cap
            append_log(f"✅ {section} Camera {selected_cam_index} started successfully.\n")
            frame._canvas.config(text="")

            if prop_var.get() and prop_var.get() != "No Properties Found":
                btn_capture.config(state="normal", text="📸 Capture & Process")
            else:
                 btn_capture.config(state="disabled", text="🚫 Select Property")

            update_feed() # Start the video feed loop

        def stop_camera():
            """Stops the current camera and cancels the update loop."""
            if frame._state.get('after_id') is not None:
                frame._canvas.after_cancel(frame._state['after_id'])
                frame._state['after_id'] = None

            cap = frame._state.get('cap')
            if cap and cap.isOpened():
                cap.release()
                frame._state['cap'] = None
                # Only log stop if it was actually running
                # append_log(f"🛑 {section} Camera stopped.\n")

            frame._canvas.config(image='', text="Camera Stopped")
            frame._canvas.imgtk = None
            if btn_capture['state'] == tk.NORMAL:
                 btn_capture.config(state="disabled", text="🚫 Camera Stopped")

        # --- Define and Attach Capture Trigger ---
        def trigger_capture_local():
             # Use the stored append_log function for the specific tab
             self._capture_and_edit(
                 frame,
                 is_entry,
                 frame._append_log, # Pass the tab-specific log function
                 prop_var.get(),
                 refresh_slots,
                 btn_capture
             )
        frame.trigger_capture = trigger_capture_local
        btn_capture.config(command=trigger_capture_local)

        frame.start_camera = start_camera
        frame.stop_camera = stop_camera
        cbcam.bind("<<ComboboxSelected>>", start_camera)

        # Load initial logs for this tab using the append_log utility
        self._load_logs(frame._log_widget, frame._append_log, is_entry)


    def _capture_and_edit(self, tab_frame, is_entry, append_log_func, prop_name, refresh_slots, btn):
        """Captures frame, detects text, shows edit dialog, and saves on confirm."""
        # Use the passed append_log_func for logging within this specific tab
        if not prop_name or prop_name == "No Properties Found":
            messagebox.showwarning("Property Required", "Please select a property before capturing.", parent=self.root)
            return

        original_btn_text = btn['text']
        btn.config(state="disabled", text="⏳ Capturing...")
        self.root.update_idletasks()

        cap = tab_frame._state.get('cap')
        if cap is None or not cap.isOpened():
            messagebox.showwarning("No Camera", "Camera is not available or not running.", parent=self.root)
            btn.config(state="normal", text=original_btn_text)
            if hasattr(tab_frame, 'start_camera'): tab_frame.start_camera()
            return

        append_log_func("📸 Capturing current frame...\n")

        captured_frame = tab_frame._state.get('frame')
        if captured_frame is None:
            print("No frame in state, attempting final read...")
            try:
                ok, captured_frame = cap.read()
                if not ok or captured_frame is None:
                     raise IOError("Final frame read failed.")
            except Exception as e:
                 messagebox.showerror("Capture Error", f"Failed to capture frame: {e}", parent=self.root)
                 append_log_func(f"❌ Capture error: {e}\n")
                 btn.config(state="normal", text=original_btn_text)
                 return

        path = None
        try:
            os.makedirs(ASSETS_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"capture_{timestamp}_{uuid.uuid4().hex[:6]}.jpg"
            path = os.path.join(ASSETS_DIR, filename)
            success = cv2.imwrite(path, captured_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not success:
                raise IOError(f"cv2.imwrite failed to save to {path}")

            append_log_func(f"💾 Frame saved: {filename}\n")
            btn.config(text="⏳ Detecting...")
            self.root.update_idletasks()

        except Exception as e:
            messagebox.showerror("File Save Error", f"Could not save captured image: {e}", parent=self.root)
            append_log_func(f"❌ Image save error: {e}\n")
            btn.config(state="normal", text=original_btn_text)
            return

        plate = detect_text(path)
        append_log_func(f"🔍 OCR Result: '{plate}'\n" if plate else "🔍 OCR Result: No plate detected\n")

        def on_confirm_dialog(edited_plate):
            append_log_func(f"✅ Confirmed Plate: {edited_plate}\n")
            # Pass the correct append_log_func to _save_record
            self._save_record(edited_plate, is_entry, append_log_func, prop_name, refresh_slots)

        def on_retake_dialog():
            append_log_func("🔄 Retake requested by user.\n")

        try:
            dialog = EditableDialog(self.root, path, plate, on_confirm_dialog, on_retake_dialog)
            dialog.bind("<Destroy>", lambda e: btn.config(state="normal", text=original_btn_text), add="+")
            self.root.wait_window(dialog)

        except Exception as e:
             messagebox.showerror("Dialog Error", f"Failed to open edit dialog: {e}", parent=self.root)
             append_log_func(f"❌ Dialog error: {e}\n")
             btn.config(state="normal", text=original_btn_text)
             if path and os.path.exists(path):
                 try: os.remove(path)
                 except Exception as del_e: print(f"Error cleaning up {path}: {del_e}")


    # <<< --- METHOD SIGNATURE UPDATED TO ACCEPT append_log_func --- >>>
    def _save_record(self, plate, is_entry, append_log_func, prop_name, refresh_slots_func):
        """Saves or updates parking record in the database with HOURLY fee logic."""
        now = datetime.now()
        # Use the passed append_log_func for logging
        append_log_func(f"💾 Attempting to save {'entry' if is_entry else 'exit'} for {plate}...\n")

        # Validate plate format again before saving
        if not re.fullmatch(r'[A-Z0-9\-]+', plate):
             messagebox.showerror("Save Error", f"Invalid plate format '{plate}' provided for saving.", parent=self.root)
             append_log_func(f"❌ Save aborted: Invalid plate format '{plate}'.\n")
             return

        try:
            prop = property_col.find_one({"name": prop_name})
            if not prop:
                messagebox.showerror("Property Error", f"Property '{prop_name}' not found in the database. Cannot save record.", parent=self.root)
                append_log_func(f"❌ Property '{prop_name}' not found in DB during save.\n")
                return

            pid = prop['_id']

            if is_entry:
                # --- Handle Vehicle Entry ---
                existing_entry = parking_col.find_one({
                    "vehicle_no": plate,
                    "property_id": str(pid),
                    "exit_time": None
                })
                if existing_entry:
                    messagebox.showwarning("Duplicate Entry", f"Vehicle {plate} already has an active parking session at {prop_name}.", parent=self.root)
                    append_log_func(f"⚠️ Duplicate entry attempt for {plate}. Already parked.\n")
                    return

                latest_prop = property_col.find_one({"_id": pid}, {"available_parking_spaces": 1})
                if not latest_prop or latest_prop.get('available_parking_spaces', 0) <= 0:
                    messagebox.showwarning("Parking Full", f"No parking slots currently available at {prop_name}.", parent=self.root)
                    append_log_func(f"❌ Parking full at {prop_name}. Entry denied for {plate}.\n")
                    return

                new_record = {
                    "parking_id": str(uuid.uuid4()),
                    "property_id": str(pid),
                    "vehicle_no": plate,
                    "entry_time": now,
                    "exit_time": None,
                    "fee": 0,
                    "mode_of_payment": None
                }
                insert_result = parking_col.insert_one(new_record)
                update_result = property_col.update_one(
                    {"_id": pid},
                    {"$inc": {"available_parking_spaces": -1}}
                )

                if insert_result.inserted_id and update_result.modified_count > 0:
                    append_log_func(f"🟢 Entry recorded: {plate} @ {now:%Y-%m-%d %H:%M:%S}\n")
                    messagebox.showinfo("Entry Success", f"Vehicle {plate} entry recorded successfully at {prop_name}.", parent=self.root)
                else:
                     append_log_func(f"⚠️ Entry DB update issue for {plate}. Check DB consistency.\n")
                     messagebox.showwarning("DB Warning", "Entry recorded, but slot count update might have failed.", parent=self.root)

            else: # is_exit
                # --- Handle Vehicle Exit ---
                updated_doc = parking_col.find_one_and_update(
                    {
                        "vehicle_no": plate,
                        "exit_time": None,
                        "property_id": str(pid)
                    },
                    {"$set": {"exit_time": now}},
                    sort=[('entry_time', -1)],
                    return_document=pymongo.ReturnDocument.AFTER
                )

                if updated_doc:
                    entry_time = updated_doc.get('entry_time')
                    calculated_fee = 0.0 # Use float for fees

                    if entry_time and isinstance(entry_time, datetime):
                        duration = now - entry_time
                        total_hours = duration.total_seconds() / 3600

                        fee_per_hour = prop.get('fee_per_hour', 10.0)
                        if not isinstance(fee_per_hour, (int, float)) or fee_per_hour < 0:
                             print(f"Warning: Invalid fee_per_hour ({fee_per_hour}) for property {prop_name}. Using default 10.0.")
                             fee_per_hour = 10.0

                        if total_hours <= 1.0:
                            calculated_fee = 0.0
                        else:
                            chargeable_hours = math.ceil(total_hours) - 1
                            calculated_fee = chargeable_hours * fee_per_hour

                        calculated_fee = round(max(0.0, calculated_fee), 2)

                        parking_col.update_one(
                            {"_id": updated_doc["_id"]},
                            {"$set": {"fee": calculated_fee}}
                        )
                        append_log_func(f"💲 Fee calculated: ₹{calculated_fee:.2f} for {total_hours:.2f} hours.\n")
                    else:
                         append_log_func(f"⚠️ Could not calculate fee for {plate}: Invalid entry time found.\n")
                         messagebox.showwarning("Fee Warning", "Could not calculate fee due to missing entry time.", parent=self.root)

                    property_col.update_one(
                        {"_id": pid},
                        {"$inc": {"available_parking_spaces": 1}}
                    )

                    # --- CURRENCY SYMBOL CORRECTED HERE ---
                    log_msg = f"🔴 Exit recorded: {plate} @ {now:%Y-%m-%d %H:%M:%S} (Fee: ₹{calculated_fee:.2f})\n"
                    append_log_func(log_msg)
                    messagebox.showinfo("Exit Success", f"Exit recorded for {plate}.\nCalculated Fee: ₹{calculated_fee:.2f}", parent=self.root)
                    # --- END CORRECTION ---

                else:
                    messagebox.showwarning("No Entry Found", f"No active parking session found for vehicle {plate} at {prop_name}.", parent=self.root)
                    append_log_func(f"❌ Exit attempt failed: No open entry found for {plate} at {prop_name}.\n")

            # Always refresh slots after entry or exit attempt
            if callable(refresh_slots_func):
                refresh_slots_func()
            else:
                 print("Warning: refresh_slots_func is not callable.")


        except pymongo.errors.ConnectionFailure as e:
             messagebox.showerror("Database Error", f"Database connection lost: {e}", parent=self.root)
             append_log_func(f"❌ DB Connection Failure: {e}\n")
        except pymongo.errors.PyMongoError as e:
             messagebox.showerror("Database Error", f"A database error occurred: {e}", parent=self.root)
             append_log_func(f"❌ DB Error during save: {e}\n")
        except Exception as e:
            messagebox.showerror("Unexpected Error", f"An unexpected error occurred while saving: {e}", parent=self.root)
            append_log_func(f"❌ Unexpected Save Error: {e}\n")
            # import traceback; traceback.print_exc() # For debugging
        finally:
            # Ensure slots are refreshed even if there was an error during DB interaction
            if callable(refresh_slots_func):
                try:
                    refresh_slots_func()
                except Exception as refresh_e:
                    print(f"Error during final slot refresh: {refresh_e}")

    # <<< --- END OF UPDATED _save_record METHOD --- >>>


    # <<< --- METHOD SIGNATURE UPDATED TO ACCEPT append_log_func --- >>>
    def _load_logs(self, log_widget, append_log_func, is_entry):
        """Loads recent parking records into the specified log display."""
        # Use the passed append_log_func for logging status during load
        section = "Entry" if is_entry else "Exit"
        # Clear existing content using the utility function (which handles state)
        log_widget.config(state=tk.NORMAL)
        log_widget.delete('1.0', tk.END)
        log_widget.insert("end", f"📄 {section} Log History:\n" + "="*25 + "\n")
        log_widget.config(state=tk.DISABLED)


        try:
            query = {"exit_time": None} if is_entry else {"exit_time": {"$ne": None}}
            sort_key = "entry_time" if is_entry else "exit_time"
            recent_records = parking_col.find(query).sort(sort_key, pymongo.DESCENDING).limit(20)

            records_list = list(recent_records)
            if not records_list:
                 append_log_func(f"No recent {section.lower()} records found.\n")
            else:
                log_lines = [] # Collect lines before inserting
                for record in records_list:
                    ts_key = "entry_time" if is_entry else "exit_time"
                    ts = record.get(ts_key)
                    if ts and isinstance(ts, datetime):
                        icon = "🟢" if is_entry else "🔴"
                        plate = record.get('vehicle_no', 'N/A')
                        time_str = ts.strftime('%Y-%m-%d %H:%M:%S')
                        log_line = f"{icon} {plate:<15} @ {time_str}"

                        if not is_entry:
                            fee = record.get('fee', None)
                            # --- CURRENCY SYMBOL CORRECTED HERE ---
                            fee_str = f"₹{fee:.2f}" if isinstance(fee, (int, float)) else "N/A"
                            log_line += f" (Fee: {fee_str})"
                            # --- END CORRECTION ---

                        log_lines.append(log_line + "\n")
                    else:
                         print(f"Skipping record due to missing/invalid timestamp: {record.get('_id')}")

                # Append all collected lines at once
                if log_lines:
                     log_widget.config(state=tk.NORMAL)
                     log_widget.insert(tk.END, "".join(log_lines))
                     log_widget.see(tk.END)
                     log_widget.config(state=tk.DISABLED)


        except pymongo.errors.ConnectionFailure as e:
             append_log_func(f"\n❌ DB Connection Error loading logs: {e}\n")
        except Exception as e:
            append_log_func(f"\n❌ Error loading logs: {e}\n")
            # import traceback; traceback.print_exc()


    def _export(self, log_widget, section):
        """Exports visible log content to a CSV file."""
        log_widget.config(state=tk.NORMAL) # Enable reading
        log_content = log_widget.get("1.0", "end").strip()
        log_widget.config(state=tk.DISABLED) # Disable again

        lines = [line for line in log_content.splitlines() if line.strip() and (line.startswith("🟢") or line.startswith("🔴"))]

        if not lines:
            messagebox.showinfo("Export Info", "No log entries found to export.", parent=self.root)
            return

        default_filename = f"{section.lower()}_logs_{datetime.now():%Y%m%d_%H%M}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title=f"Save {section} Logs As",
            initialfile=default_filename,
            parent=self.root
        )
        if not path:
            return

        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                header = ["Action", "Plate", "Timestamp"]
                if section == "Exit":
                    header.append("Fee (₹)") # Corrected header
                writer.writerow(header)

                for line in lines:
                    # Updated Regex to correctly capture ₹ symbol and fee
                    pattern = r'([🟢🔴])\s+([A-Z0-9\-]+)\s+@\s+([\d\-]+\s+[\d:]+)(?:\s+\(Fee:\s*₹([\d\.]+)\))?'
                    match = re.match(pattern, line.strip())

                    if match:
                        icon, plate, timestamp, fee = match.groups()
                        action = "Entry" if icon == "🟢" else "Exit"
                        row_data = [action, plate.strip(), timestamp.strip()]
                        if section == "Exit":
                            row_data.append(fee.strip() if fee else "")
                        writer.writerow(row_data)
                    else:
                        print(f"Skipping malformed log line during export: {line}")

            messagebox.showinfo("Export Success", f"Logs successfully exported to:\n{path}", parent=self.root)

        except Exception as e:
            messagebox.showerror("Export Error", f"An error occurred during CSV export: {e}", parent=self.root)
            print(f"Export error: {e}")


# --- Main Execution ---
if __name__ == "__main__":
    if db is None or client is None:
         print("Exiting: Database connection not established.")
         sys.exit(1)

    root = tk.Tk()
    app = ParkingApp(root)

    def on_closing():
        """Gracefully handle application closing."""
        print("Closing application...")
        for tab in (app.entry_tab, app.exit_tab):
            if tab and hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                try:
                    print(f"Stopping camera for tab: {tab}")
                    tab.stop_camera()
                except Exception as e:
                    print(f"Error stopping camera during shutdown for {tab}: {e}")

        global client
        if client:
            try:
                client.close()
                print("MongoDB connection closed.")
            except Exception as e:
                print(f"Error closing MongoDB connection: {e}")
            client = None

        root.destroy()
        print("Application closed.")

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
