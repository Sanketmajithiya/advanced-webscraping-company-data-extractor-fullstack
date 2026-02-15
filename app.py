
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
import os
import threading
import pandas as pd
from scraper import run_scraper

app = Flask(__name__)


# Global variable to store the latest generated file and progress
LATEST_FILE = None
IS_SCRAPING = False
SCRAPING_LOCK = threading.Lock()
SCRAPER_PROGRESS = {
    "total": 0,
    "processed": 0,
    "current_area": "",
    "log": [],
    "status": "Idle"
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scrape', methods=['POST'])
def scrape():
    global LATEST_FILE, IS_SCRAPING, SCRAPER_PROGRESS
    
    if IS_SCRAPING:
        return jsonify({"status": "error", "message": "Scraper is already running"}), 409

    data = request.json
    areas = data.get('areas', [])
    city = data.get('city', 'Surat')
    category = data.get('category', 'it')
    custom_query = data.get('custom_query', '')
    
    if not areas:
        return jsonify({"status": "error", "message": "No areas provided"}), 400
        
    def run_task():
        global LATEST_FILE, IS_SCRAPING, SCRAPER_PROGRESS
        with SCRAPING_LOCK:
            IS_SCRAPING = True
            LATEST_FILE = None # Reset previous file
            # Reset progress
            SCRAPER_PROGRESS = {
                "total": 0,
                "processed": 0,
                "current_area": "Initializing...",
                "log": ["Starting scraper..."],
                "status": "Running"
            }
            
            def progress_callback(update_data):
                """Callback to update progress from scraper"""
                global SCRAPER_PROGRESS
                # Update fields if provided
                if "total" in update_data: SCRAPER_PROGRESS["total"] = update_data["total"]
                if "processed" in update_data: SCRAPER_PROGRESS["processed"] = update_data["processed"]
                if "current_area" in update_data: SCRAPER_PROGRESS["current_area"] = update_data["current_area"]
                if "log" in update_data: 
                    SCRAPER_PROGRESS["log"].append(update_data["log"])
                    # Keep log size manageable
                    if len(SCRAPER_PROGRESS["log"]) > 50:
                        SCRAPER_PROGRESS["log"].pop(0)

            try:
                # Call the scraper with callback
                filename = run_scraper(
                    areas=areas,
                    city=city,
                    category=category,
                    custom_query=custom_query,
                    progress_callback=progress_callback
                )
                print(f"DEBUG: Scraper returned filename: {filename}")
                LATEST_FILE = filename
                SCRAPER_PROGRESS["status"] = "Completed"
                SCRAPER_PROGRESS["log"].append("Scraping completed successfully!")
            except Exception as e:
                SCRAPER_PROGRESS["status"] = "Failed"
                SCRAPER_PROGRESS["log"].append(f"Error: {str(e)}")
            finally:
                IS_SCRAPING = False
                
    # Run in background thread
    thread = threading.Thread(target=run_task)
    thread.start()
    
    return jsonify({"status": "success", "message": "Scraping started"})

@app.route('/api/status')
def status():
    global IS_SCRAPING, LATEST_FILE, SCRAPER_PROGRESS
    
    # Logic to persist latest file across restarts
    if LATEST_FILE is None and not IS_SCRAPING:
        try:
            # Find the most recent Excel file in the current directory (matches any city)
            files = [f for f in os.listdir('.') if f.endswith('.xlsx') and '_data_' in f]
            if files:
                # Sort by modification time (newest first)
                latest = max(files, key=os.path.getmtime)
                LATEST_FILE = latest
                print(f"DEBUG: Found existing file: {LATEST_FILE}")
            else:
                print("DEBUG: No existing files found.")
        except Exception as e:
            print(f"Error finding latest file: {e}")

    # FORCE CHECK: If status says Completed but LATEST_FILE is None, try finding it again
    if SCRAPER_PROGRESS["status"] == "Completed" and LATEST_FILE is None:
         print("DEBUG: Status is Completed but LATEST_FILE is None. Retrying file search...")
         try:
            files = [f for f in os.listdir('.') if f.endswith('.xlsx') and '_data_' in f]
            if files:
                latest = max(files, key=os.path.getmtime)
                LATEST_FILE = latest
                print(f"DEBUG: Recovered file: {LATEST_FILE}")
         except Exception as e:
            print(f"Error recovering file: {e}")

    return jsonify({
        "is_scraping": IS_SCRAPING,
        "latest_file": LATEST_FILE,
        "progress": SCRAPER_PROGRESS
    })

@app.route('/api/download/<filename>')
def download(filename):
    if os.path.exists(filename):
        return send_file(filename, as_attachment=True)
    return jsonify({"status": "error", "message": "File not found"}), 404

@app.route('/api/view/<filename>')
def view_data(filename):
    if os.path.exists(filename):
        try:
            df = pd.read_excel(filename)
            # Replace NaN with empty string for JSON serialization
            df = df.fillna("")
            return jsonify({
                "status": "success",
                "data": df.to_dict(orient='records'),
                "columns": df.columns.tolist()
            })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "File not found"}), 404

if __name__ == '__main__':
    # Ensure templates and static folders exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    # Only print banner in the reloader process (child) to avoid duplicates
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        print("\n" + "="*50)
        print("🚀  SURAT DATA EXTRACTOR - READY")
        print("="*50 + "\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
