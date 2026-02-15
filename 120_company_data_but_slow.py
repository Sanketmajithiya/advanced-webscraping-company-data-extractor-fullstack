


# Web_scraping_Dynamic_improved_grouped.py
# Improved: hybrid threaded + grouped output by area + stricter name cleaning

import os
import re
import time
import random
import requests
import pandas as pd
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, ElementClickInterceptedException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import urllib3

# ---------------------- CONFIG ----------------------
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# driver = create_driver(headless=False)


# Put areas in the order you want them in Excel output:
AREAS = ["vesu", "adajan"]
# AREAS = ["dindoli"]

MAX_LISTINGS = 120
BROWSER_INSTANCES = 2       # how many parallel Chrome instances to run at once
MAX_THREADS = 8             # threads for domain/email checking
CANDIDATE_TLDS = [".com", ".in", ".co.in", ".net", ".org"]
REQUEST_TIMEOUT = 6
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
]
# ----------------------------------------------------

# ---------------------- UTILITIES ----------------------
def clean_unicode_symbols(text):
    if not text:
        return ""
    # remove non-ascii and weird control characters
    s = re.sub(r'[^\x00-\x7F]+', '', text)
    # collapse multiple spaces/newlines into single space
    s = re.sub(r'\s+', ' ', s).strip()
    # strip leading/trailing punctuation left over
    s = s.strip(" \t\n\r-–—:;,.|")
    return s

def get_next_available_filename(base_name):
    today = date.today().strftime("%Y-%m-%d")
    filename = f"{base_name}_{today}.xlsx"
    if not os.path.exists(filename):
        return filename
    counter = 1
    while True:
        new_filename = f"{base_name}_{today}_{counter}.xlsx"
        if not os.path.exists(new_filename):
            return new_filename
        counter += 1

def clean_url_keep_scheme_and_netloc(path_url):
    try:
        parsed = urlparse(path_url)
        clean = urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path or "/", "", "", ""))
        return clean.rstrip("/")
    except Exception:
        return path_url

def is_probably_social(url):
    if not url:
        return False
    s = url.lower()
    bad = ['facebook.com', 'linkedin.com', 'instagram.com', 'youtube.com', 'g.page', 'maps.google.com', 'google.com/maps', 'pages.app']
    return any(b in s for b in bad)

# ---------------------- SELENIUM DRIVER ----------------------
def create_driver(headless=True, user_agent=None):
    options = Options()
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en")
    if headless:
        options.add_argument("--headless=new")
    ua = user_agent or random.choice(UA_POOL)
    options.add_argument(f"user-agent={ua}")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.wait = WebDriverWait(driver, 15)
    return driver

# ---------------------- HTTP DOMAIN CHECKER ----------------------
def is_domain_live_requests(hostname, timeout=REQUEST_TIMEOUT, max_retries=2):
    if not hostname:
        return False
    parsed = urlparse(hostname)
    if parsed.scheme and parsed.netloc:
        base = parsed.netloc
    else:
        base = hostname.replace("http://", "").replace("https://", "").lstrip("www.").strip("/")

    combos = [
        f"http://{base}",
        f"https://{base}",
        f"http://www.{base}",
        f"https://www.{base}"
    ]
    for url in combos:
        for attempt in range(max_retries + 1):
            try:
                headers = {"User-Agent": random.choice(UA_POOL)}
                resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True, verify=False)
                if 200 <= resp.status_code < 400:
                    return clean_url_keep_scheme_and_netloc(resp.url)
                resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, verify=False)
                if 200 <= resp.status_code < 400:
                    return clean_url_keep_scheme_and_netloc(resp.url)
            except requests.RequestException:
                time.sleep(0.35 * (attempt + 1))
                continue
    return False

# ---------------------- DOMAIN GENERATOR ----------------------
def generate_candidate_domains(company_name, area_hint=None):
    if not company_name:
        return []
    name = re.sub(r"\b(pvt|pvt\.|ltd|ltd\.|llp|private|company|co|services|solutions|technologies|tech|the|software|systems)\b", "", company_name, flags=re.I)
    cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", name)
    parts = [p for p in cleaned.split() if p]
    bases = []
    if parts:
        joined = "".join(parts).lower()
        hyphen = "-".join(parts).lower()
        initials = "".join(p[0] for p in parts).lower() if len(parts) <= 4 else ""
        first_two = "".join(parts[:2]).lower() if len(parts) >= 2 else joined
        for b in [joined, hyphen, initials, first_two]:
            if b and b not in bases:
                bases.append(b)
    candidates = []
    for base in bases:
        for t in CANDIDATE_TLDS:
            candidates.append(base + t)
            candidates.append("www." + base + t)
    if area_hint:
        area = re.sub(r"[^A-Za-z0-9]", "", area_hint).lower()
        for base in bases:
            candidates.append(f"{base}{area}.com")
            candidates.append(f"{base}-{area}.com")
    # unique preserve order
    seen = set(); out = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out

# ---------------------- EMAIL EXTRACTION ----------------------
def extract_emails_via_requests_from_url(url, timeout=REQUEST_TIMEOUT):
    if not url:
        return []
    url = url if url.startswith("http") else "https://" + url
    try:
        headers = {"User-Agent": random.choice(UA_POOL)}
        r = requests.get(url, headers=headers, timeout=timeout, verify=False)
        text = r.text or ""
        emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)
        filtered = [e for e in emails if any(k in e.lower() for k in ("contact","info","support","enquiry","hello","admin","sales"))]
        return list(dict.fromkeys(filtered or emails))
    except requests.RequestException:
        return []

def extract_contact_email_selenium(website_url):
    if not website_url:
        return "Not Found"
    url = website_url if website_url.startswith("http") else "https://" + website_url
    try:
        driver = create_driver(headless=True)
    except WebDriverException:
        return "Not Found"
    try:
        driver.get(url)
        driver.wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        soup = BeautifulSoup(driver.page_source or "", "html.parser")
        page_text = soup.get_text(separator="\n")
        emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", page_text)
        filtered = [e for e in emails if any(k in e.lower() for k in ("contact","info","support","enquiry","hello","admin","sales"))]
        try: driver.quit()
        except: pass
        final = filtered or emails
        return ", ".join(dict.fromkeys(final)) if final else "Not Found"
    except Exception:
        try: driver.quit()
        except: pass
        return "Not Found"

# ---------------------- TRY DOMAINS (threaded) ----------------------
def threaded_try_domains_and_get_email(company_name, area_hint=None):
    candidates = generate_candidate_domains(company_name, area_hint)
    if not candidates:
        return "Not Found", "Not Found"
    result = {"website": "Not Found", "email": "Not Found"}

    def check_domain(domain):
        domain = domain.strip()
        if not domain: return None
        working = is_domain_live_requests(domain)
        if not working:
            working = is_domain_live_requests(domain.replace("www.", ""))
        if not working:
            return None
        working_clean = clean_url_keep_scheme_and_netloc(working)
        if is_probably_social(working_clean):
            return None
        emails = extract_emails_via_requests_from_url(working_clean)
        if emails:
            return {"website": working_clean, "email": ", ".join(emails)}
        email_s = extract_contact_email_selenium(working_clean)
        if email_s and email_s != "Not Found":
            return {"website": working_clean, "email": email_s}
        return {"website": working_clean, "email": "Not Found"}

    with ThreadPoolExecutor(max_workers=min(MAX_THREADS, max(2, len(candidates)))) as ex:
        futures = {ex.submit(check_domain, c): c for c in candidates}
        for future in as_completed(futures):
            try:
                res = future.result()
                if res and res.get("website") and res["website"] != "Not Found":
                    result.update(res)
                    for f in futures:
                        try: f.cancel()
                        except: pass
                    break
            except Exception:
                continue

    if result["website"] == "Not Found":
        return ", ".join(candidates[:6]), "Not Found"
    return result["website"], result["email"]

# ---------------------- GOOGLE MAPS HELPERS ----------------------
def extract_address_phone(driver):
    address, phone = "Not Found", "Not Found"
    try:
        addr_el = driver.find_elements(By.XPATH, "//button[contains(@data-item-id, 'address')]")
        if addr_el:
            address = addr_el[0].text.strip()
    except: pass
    try:
        ph_el = driver.find_elements(By.XPATH, "//button[contains(@data-item-id, 'phone')]")
        if ph_el:
            phone = ph_el[0].text.strip()
    except: pass
    if address == "Not Found":
        try:
            addr_el2 = driver.find_elements(By.XPATH, "//div[contains(@class,'Io6YTe')]")
            for el in addr_el2:
                if re.search(r"\d{6}", el.text) or "India" in el.text:
                    address = el.text.strip(); break
        except: pass
    if phone == "Not Found":
        try:
            ph_el2 = driver.find_elements(By.XPATH, "//div[contains(@class,'Io6YTe')]")
            for el in ph_el2:
                if re.search(r"\+?\d{5,}", el.text):
                    phone = el.text.strip(); break
        except: pass
    return clean_unicode_symbols(address), clean_unicode_symbols(phone)

def safe_click(driver, element, max_retries=3):
    for attempt in range(max_retries):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            time.sleep(0.15 + random.random() * 0.2)
            element.click()
            return True
        except ElementClickInterceptedException:
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                time.sleep(0.6 + 0.2 * attempt)
                continue
        except Exception:
            time.sleep(0.3)
            continue
    return False

def is_valid_company_website(url):
    if not url or url in ("Not Found", ""):
        return False
    u = url.lower()
    if is_probably_social(u):
        return False
    return u.startswith("http") and "." in urlparse(u).netloc

# ---------------------- MAIN SCRAPER (per area) ----------------------
def scrape_google_maps_main_for_area(area, max_listings=50, headless=True):
    query = f"{area} IT companies"
    try:
        driver = create_driver(headless=headless)
    except WebDriverException as e:
        print(f"⚠️ Failed to start browser for {area}: {e}")
        return []

    try:
        driver.get("https://www.google.com/maps")
        driver.wait.until(EC.presence_of_element_located((By.ID, "searchboxinput")))
    except Exception as e:
        print(f"⚠️ Maps load failed for {area}: {e}")
        try: driver.quit()
        except: pass
        return []

    search_box = driver.find_element(By.ID, "searchboxinput")
    search_box.clear()
    search_box.send_keys(query)
    search_box.send_keys(Keys.ENTER)
    time.sleep(3 + random.random() * 2)

    try:
        scrollable_div_xpath = "//div[@role='feed']"
        scrollable = driver.wait.until(EC.presence_of_element_located((By.XPATH, scrollable_div_xpath)))
    except Exception as e:
        print(f"⚠️ Cannot find listings pane for {area}: {e}")
        driver.quit()
        return []

    prev_count = 0
    listings = []
    stuck_rounds = 0
    for _ in range(60):
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scrollable)
        time.sleep(2.5 + random.random() * 1.5)
        listings = driver.find_elements(By.XPATH, "//div[contains(@class,'Nv2PK')]")
        if len(listings) >= max_listings:
            break
        if len(listings) == prev_count:
            stuck_rounds += 1
            if stuck_rounds >= 5:   # instead of 1-2, allow 5 slow rounds
                break
        else:
            stuck_rounds = 0
        prev_count = len(listings)

    print(f"✅ Found {len(listings)} listings in {area}")

    company_data = []
    seen = set()
    for idx, listing in enumerate(listings[:max_listings]):
        try:
            if not safe_click(driver, listing):
                print(f"❌ Could not click listing {idx+1} in {area}")
                continue
            time.sleep(1.6 + random.random() * 1.6)

            name_el = driver.wait.until(EC.visibility_of_element_located((By.XPATH, "//h1[contains(@class, 'DUwDvf')]")))
            raw_name = name_el.text.strip()
            name = clean_unicode_symbols(raw_name)
            # collapse any repeated punctuation or stray characters
            name = re.sub(r'[\|\/\\]+', ' ', name).strip()

            key = f"{area.lower()}__{name.lower()}"
            if key in seen:
                back_btn = driver.find_elements(By.XPATH, "//button[@aria-label='Back']")
                if back_btn:
                    try: back_btn[0].click(); time.sleep(0.6)
                    except: pass
                continue
            seen.add(key)

            address, phone = extract_address_phone(driver)
            website_el = driver.find_elements(By.XPATH, "//a[contains(@data-item-id, 'authority')]")
            raw_website = website_el[0].get_attribute("href") if website_el else "Not Found"
            final_website_record = "Not Found"
            email_found = "Not Found"

            if raw_website and raw_website != "Not Found":
                clean_w = clean_url_keep_scheme_and_netloc(raw_website)
                if is_valid_company_website(clean_w):
                    final_website_record = clean_w
                    emails = extract_emails_via_requests_from_url(final_website_record)
                    if emails:
                        email_found = ", ".join(emails)
                    else:
                        email_found = extract_contact_email_selenium(final_website_record)
                else:
                    final_website_record, email_found = threaded_try_domains_and_get_email(name, area_hint=area)
            else:
                final_website_record, email_found = threaded_try_domains_and_get_email(name, area_hint=area)

            if isinstance(final_website_record, str) and final_website_record.startswith("http"):
                final_website_record = clean_url_keep_scheme_and_netloc(final_website_record)

            if is_valid_company_website(final_website_record):
                company_data.append({
                    "Area": area.capitalize(),
                    "Company Name": name,
                    "Address": address,
                    "Phone (Maps)": phone,
                    "Website": final_website_record,
                    "Email (Website)": email_found or "Not Found"
                })
                print(f"➡️ {area} | {idx+1}. {name} | {address} | {phone} | {final_website_record} | {email_found}")
            else:
                print(f"❌ Skipping '{name}' in {area} — no valid working website found")

            back_btn = driver.find_elements(By.XPATH, "//button[@aria-label='Back']")
            if back_btn:
                try:
                    back_btn[0].click()
                    time.sleep(0.6 + random.random() * 0.6)
                except:
                    pass

        except Exception as e:
            print(f"❌ Error in {area} listing {idx+1}: {e}")
            try:
                back_btn = driver.find_elements(By.XPATH, "//button[@aria-label='Back']")
                if back_btn:
                    back_btn[0].click(); time.sleep(0.6)
            except:
                pass
            continue

    try:
        driver.quit()
    except:
        pass
    return company_data

# ---------------------- HYBRID MULTI-AREA ----------------------
def run_for_areas_hybrid(areas_list, max_listings=MAX_LISTINGS, browser_instances=BROWSER_INSTANCES, headless=True):
    """
    Run scrapers in chunks. Return mapping area -> list-of-companies so we can
    assemble Excel grouped by given area order.
    """
    area_map = {a: [] for a in areas_list}
    chunks = [areas_list[i:i + browser_instances] for i in range(0, len(areas_list), browser_instances)]
    for chunk in chunks:
        with ThreadPoolExecutor(max_workers=len(chunk)) as ex:
            futures = {ex.submit(scrape_google_maps_main_for_area, area, max_listings, headless): area for area in chunk}
            for future in as_completed(futures):
                area = futures[future]
                try:
                    res = future.result()
                    if res:
                        area_map[area].extend(res)
                except Exception as e:
                    print(f"⚠️ Error running area {area}: {e}")
        time.sleep(2 + random.random() * 1.5)
    return area_map

# ---------------------- MAIN ----------------------
# ---------------------- MAIN ----------------------
if __name__ == "__main__":
    try:
        total_start = time.time()
        print("🚀 Starting improved grouped scraping...")
        print("DevTools listening on ws://127.0.0.1:56636/devtools/browser/f70b4568-e52c-433c-b30c-450c6c07682a")

        area_results_map = {}
        for area in AREAS:
            print(f"\n⏳ Starting area: {area} ...")
            area_start = time.time()

            # Run scraping for each area
            res = run_for_areas_hybrid([area], max_listings=MAX_LISTINGS,
                                       browser_instances=BROWSER_INSTANCES, headless=True)
            area_results_map[area] = res.get(area, [])

            area_time = round((time.time() - area_start) / 60, 2)
            print(f"📧 Email extraction for {area} took {area_time:.2f} minutes")
            print(f"✅ Total time for area {area}: {area_time:.2f} minutes")

        # Combine and deduplicate results area-wise
        final_rows = []
        for area in AREAS:
            seen_companies = set()
            rows = area_results_map.get(area, [])
            for r in rows:
                name = r.get("Company Name", "").strip()
                if name.lower() in seen_companies:
                    continue
                seen_companies.add(name.lower())
                final_rows.append({
                    "Area": r.get("Area", area.capitalize()),
                    "Company Name": name,
                    "Address": r.get("Address", "Not Found"),
                    "Phone (Maps)": r.get("Phone (Maps)", "Not Found"),
                    "Website": r.get("Website", "Not Found"),
                    "Email (Website)": r.get("Email (Website)", "Not Found")
                })

        # Save to Excel
        df = pd.DataFrame(final_rows, columns=[
            "Area", "Company Name", "Address", "Phone (Maps)", "Website", "Email (Website)"
        ])
        filename = get_next_available_filename("surat_company_contacts")
        df.to_excel(filename, index=False, engine='openpyxl')

        total_end = time.time()
        total_minutes = round((total_end - total_start) / 60, 2)
        total_hours = round(total_minutes / 60, 2)
        end_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(total_end))

        print(f"\n📁 Saved {len(df)} rows to: {filename}")
        print(f"⏱ Script finished at: {end_time_str}")
        print(f"⏳ Total execution time: {total_minutes:.2f} minutes (~{total_hours:.2f} hours)")

    except KeyboardInterrupt:
        print("⛔ Interrupted by user.")
    except Exception as e:
        print(f"⚠️ Unexpected error: {e}")



