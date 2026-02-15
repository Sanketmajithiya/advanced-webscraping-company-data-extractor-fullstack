import os
import re
import time
import random
import pandas as pd
import requests
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse
from datetime import datetime
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if os.path.exists('scraper.log'):
    try:
        os.remove('scraper.log')
    except:
        pass

logging.basicConfig(
    filename='scraper.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

CONFIG = {
    "AREAS": ["Adajan"], # Default
    "SEARCH_QUERY_TEMPLATE": "IT companies in {area} surat", 
    "TARGET_PER_AREA_MIN": 120,  # TEST MODE: Changed from 120
    "TARGET_PER_AREA_MAX": 120,  # TEST MODE: Changed from 120
    "MAX_THREADS": 20,
    "REQUEST_TIMEOUT": 8,
    "HEADLESS":True, # User explicitly requested visibility Code work better with true 
    "BROWSER_INSTANCES": 2
}

AREAS = CONFIG["AREAS"]
SEARCH_QUERY_TEMPLATE = CONFIG["SEARCH_QUERY_TEMPLATE"]
TARGET_PER_AREA_MIN = CONFIG["TARGET_PER_AREA_MIN"]
TARGET_PER_AREA_MAX = CONFIG["TARGET_PER_AREA_MAX"]
MAX_THREADS = CONFIG["MAX_THREADS"]
REQUEST_TIMEOUT = CONFIG["REQUEST_TIMEOUT"]

HEADLESS = CONFIG["HEADLESS"]
BROWSER_INSTANCES = CONFIG["BROWSER_INSTANCES"]

CANDIDATE_TLDS = [".com", ".in", ".co.in", ".net", ".org", ".biz", ".info"]

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/122.0"
]

session = requests.Session()

def check_internet_speed():
    print("🌐 Checking network connection...")
    try:
        start = time.time()
        requests.get("https://www.google.com", timeout=5)
        latency = (time.time() - start) * 1000
        print(f" Network is Online (Latency: {latency:.0f}ms)")
        if latency > 500:
            print("⚠️ Warning: High Latency. Scraper might be slow.")
        return True
    except Exception as e:
        print(" Network Error: No Internet Connection!")
        print(f"   Error: {e}")
        return False


def clean_url(url):
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, "/", "", "", ""))
    except:
        return url

def is_social_or_google(url):
    if not url:
        return True
    bad = [
        "facebook.com", "instagram.com", "linkedin.com",
        "youtube.com", "google.com", "maps.google",
        "g.page", "justdial.com", "indiamart.com", "sulekha.com"
    ]
    return any(b in url.lower() for b in bad)

def generate_candidate_domains(company_name, area_hint=None):
    if not company_name:
        return []

    name = re.sub(
        r"\b(pvt|ltd|llp|private|company|co|services|solutions|technologies|tech|software|systems|the)\b",
        "",
        company_name,
        flags=re.I
    )

    cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", name)
    parts = [p.lower() for p in cleaned.split() if p]

    bases = set()
    if parts:
        bases.add("".join(parts))
        bases.add("-".join(parts))
        if len(parts) >= 2:
            bases.add(parts[0] + parts[1])

    domains = []
    for base in bases:
        for tld in CANDIDATE_TLDS:
            domains.append(base + tld)
            domains.append("www." + base + tld)

    if area_hint:
        area = re.sub(r"[^a-z0-9]", "", area_hint.lower())
        for base in bases:
            domains.append(f"{base}{area}.com")
            domains.append(f"{base}-{area}.com")

    return list(dict.fromkeys(domains))

def is_domain_live(domain):
    if not domain:
        return False

    base = domain.replace("http://", "").replace("https://", "").strip("/")
    urls = [
        f"http://{base}",
        f"https://{base}",
        f"http://www.{base}",
        f"https://www.{base}",
    ]

    headers = {"User-Agent": random.choice(UA_POOL)}

    for url in urls:
        try:
            r = session.head(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True, verify=False)
            if 200 <= r.status_code < 400:
                return clean_url(r.url)

            r = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True, verify=False)
            if 200 <= r.status_code < 400:
                return clean_url(r.url)
        except:
            continue

    return False

def extract_emails_from_url(url):
    # Simple cache to avoid re-scraping same URLs
    if not hasattr(extract_emails_from_url, '_cache'):
        extract_emails_from_url._cache = {}
    
    if url in extract_emails_from_url._cache:
        return extract_emails_from_url._cache[url]
    
    extracted_emails = set()
    visited_urls = set()
    contact_keywords = re.compile(r'contact|about|touch|connect|reach|support', re.IGNORECASE)
    
    def decode_cloudflare_email(encoded_string):
        """Decode CloudFlare protected emails"""
        try:
            r = int(encoded_string[:2], 16)
            email = ''.join([chr(int(encoded_string[i:i+2], 16) ^ r) for i in range(2, len(encoded_string), 2)])
            return email
        except:
            return None
    
    def get_page_emails(target_url):
        if target_url in visited_urls:
            return
        visited_urls.add(target_url)
        
        try:
            headers = {"User-Agent": random.choice(UA_POOL)}
            r = session.get(target_url, headers=headers, timeout=10, verify=False)
            if r.status_code != 200:
                return
            html = r.text
            
            # 1. Extract emails with regex
            found = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", html)
            
            # 2. Extract CloudFlare protected emails
            cf_emails = re.findall(r'/cdn-cgi/l/email-protection#([a-f0-9]+)', html)
            for cf_code in cf_emails:
                decoded = decode_cloudflare_email(cf_code)
                if decoded:
                    found.append(decoded)
            
            # 3. Extract from mailto links using BeautifulSoup
            try:
                soup = BeautifulSoup(html, 'html.parser')
                for link in soup.find_all('a', href=True):
                    if link['href'].startswith('mailto:'):
                        email = link['href'].replace('mailto:', '').split('?')[0]
                        found.append(email)
            except:
                pass
            
            junk_keywords = [
                'bootstrap', 'sentry', 'example', 'domain', 'react', 'jquery', 
                'node_modules', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp',
                'wix', 'shopify', 'godaddy', 'namecheap'
            ]
            for e in found:
                e_lower = e.lower()
                if any(junk in e_lower for junk in junk_keywords):
                    continue
                try:
                    domain_part = e_lower.split('@')[1]
                    if domain_part[0].isdigit(): 
                         continue
                except:
                    pass
                
                extracted_emails.add(e_lower)
            return r.text 
        except:
            return ""

    homepage_html = get_page_emails(url)
    
    if homepage_html:
        try:
            soup = BeautifulSoup(homepage_html, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                text = a.get_text(" ", strip=True)

                if contact_keywords.search(text) or contact_keywords.search(href):
                     from urllib.parse import urljoin
                     full_link = urljoin(url, href)
                     
                     if full_link not in visited_urls and url in full_link:
                         get_page_emails(full_link)
                         if len(visited_urls) >= 3:
                             break
        except:
            pass

    final_emails = sorted(list(extracted_emails), key=lambda x: len(x))[:3]
    extract_emails_from_url._cache[url] = final_emails
    return final_emails

def auto_find_website_and_email(company_name, area_hint=None):
    domains = generate_candidate_domains(company_name, area_hint)
    def check(domain):
        live = is_domain_live(domain)
        if not live: return None
        if is_social_or_google(live): return None
        emails = extract_emails_from_url(live)
        return {
            "website": live,
            "email": ", ".join(emails) if emails else "Not Found"
        }
    with ThreadPoolExecutor(max_workers=min(MAX_THREADS, len(domains))) as executor:
        futures = [executor.submit(check, d) for d in domains]
        for future in as_completed(futures):
            result = future.result()
            if result:
                return result["website"], result["email"]
    return "Not Found", "Not Found"

def get_domain_from_url(url):
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        return domain
    except:
        return ""

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'[^\x00-\x7F\u0900-\u097F]+', ' ', text)    
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\w\s.,#\-()&/\'"]', '', text)
    return text

def create_driver(headless=None):
    if headless is None:
        headless = CONFIG["HEADLESS"]

    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument("--force-device-scale-factor=0.8")
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    ]
    options.add_argument(f"user-agent={random.choice(user_agents)}")
    
    # User requested persistent fresh sessions to avoid cache issues
    options.add_argument("--incognito")
    
    if headless:
        options.add_argument("--headless=new")
    
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": random.choice(user_agents)
        })
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        logging.info(" Driver created successfully")
        return driver
    except Exception as e:
        logging.error(f" Driver creation failed: {e}")
        return None


def get_phone_number_from_page(driver):
    phone = "Not Found"
    try:
        time.sleep(0.5)
        try:
            details_pane = driver.find_element(By.XPATH, "//div[@role='main'] | //div[contains(@class, 'bJzME')]")
            pane_source = details_pane.get_attribute("innerHTML")
            pane_text = details_pane.text
        except:
            pane_source = driver.page_source
            pane_text = driver.find_element(By.TAG_NAME, 'body').text

        try:
            phone_btns = driver.find_elements(By.XPATH, "//button[contains(@data-item-id, 'phone')]")
            if phone_btns:
                for btn in phone_btns:
                    if btn.is_displayed():
                        text = btn.text or btn.get_attribute("aria-label") or ""
                        nums = re.findall(r'\d{5}\s*\d{5}|\d{10}', text)
                        if nums:
                             return f"{nums[0][:5]} {nums[0][5:]}"
        except:
             pass

        phone_patterns = [
            r'Phone[:\s]*([+\d\s\-]{10,20})',
            r'\+91\s*\d{5}\s*\d{5}',
            r'\b\d{5}\s*\d{5}\b',
            r'tel:([+\d]+)',
            r'\b0\d{2,4}[-\s]+\d{6,8}\b',
            r'\b\d{4}[-\s]+\d{7}\b'
        ]
        
        for pattern in phone_patterns:
            matches = re.findall(pattern, pane_source, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple): match = match[0]
                clean_num = re.sub(r'\D', '', str(match))
                if len(clean_num) >= 10:
                     if len(clean_num) > 10 and clean_num.startswith('91'):
                         clean_num = clean_num[2:]
                     if len(clean_num) >= 10:
                         return f"{clean_num[:5]} {clean_num[5:]}"
                         
        for line in pane_text.split('\n'):
             if re.search(r'\d{5}\s*\d{5}', line) or re.search(r'0\d{2,4}[-\s]+\d{6,8}', line):
                 clean_num = re.sub(r'\D', '', line)
                 if len(clean_num) >= 10:
                      return f"{clean_num[:5]} {clean_num[5:]}"
    except Exception as e:
        pass
    return phone

def get_address_from_page(driver):
    address = "Not Found"
    address_selectors = [
        "//button[@data-item-id='address']//div",
        "//button[contains(@data-item-id, 'address')]",
        "//div[@data-tooltip='Copy address']//div",
        "//div[contains(@class, 'rogA2c')]//div[contains(@class, 'Io6YTe')]",
        "//div[contains(@class, 'fontBodyMedium')][contains(., 'Surat') or contains(., 'Gujarat') or contains(., 'India')]",
        "//div[contains(@class, 'CsEnBe')]",
        "//div[contains(@class, 'AeaXub')]",
    ]

    for selector in address_selectors:
        try:
            elements = driver.find_elements(By.XPATH, selector)
            for elem in elements:
                text = elem.text.strip()
                if text and len(text) > 20: 
                    if any(keyword in text for keyword in ['Surat', 'Gujarat', 'Road', 'Street', 'Area', 'Society', 'Plot']):
                        address = clean_address(text)
                        if address != "Not Found":
                            return address
        except:
            continue
    
    try:
        all_text = driver.find_element(By.TAG_NAME, 'body').text
        lines = all_text.split('\n')
        for line in lines:
            line = line.strip()
            if len(line) > 30 and ('Surat' in line or 'Gujarat' in line or re.search(r'\d{6}', line)):
                address = clean_address(line)
                if address != "Not Found":
                    return address
    except:
        pass
    return address

def get_website_from_page(driver):
    website = "Not Found"
    try:
        time.sleep(0.5)
        prefix = "//div[@role='main']"
        website_selectors = [
            f"{prefix}//a[contains(@data-item-id, 'authority') and @href]",
            f"{prefix}//a[@data-tooltip='Open website' and @href]",
        ]
        
        for selector in website_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for el in elements:
                    href = el.get_attribute("href")
                    if href and href.startswith('http'):
                        href = href.split('?')[0].split('&')[0].strip()
                        if not is_generic_website(href):
                            return clean_url(href)
            except:
                continue 
    except:
        pass
    return website

def is_generic_website(url):
    generic_patterns = [
        'indiamart.com', 'justdial.com', 'tradeindia.com',
        'sulekha.com', 'quikr.com', 'olx.in', 'yellowpages.in',
        'g.page', 'maps.google.com', 'facebook.com', 'instagram.com',
        'linkedin.com', 'youtube.com', 'twitter.com'
    ]
    url_lower = url.lower()
    for pattern in generic_patterns:
        if pattern in url_lower:
            return True
    return False

def clean_address(address):
    if not address or address == "Not Found":
        return "Not Found"
    

    address = re.sub(r'[^\w\s,.\-/()&\'\"]', '', address, flags=re.UNICODE)
    
    address = re.sub(r'\b[A-Z0-9]{4,}\+[A-Z0-9]{2,}\b', '', address)
    address = re.sub(r'[•|📍]', '', address)
    address = re.sub(r'\s+', ' ', address).strip()
    address = re.sub(r'^,\s*', '', address)
    return address

def should_skip_company(company_name):
    if not company_name or company_name == "Not Found":
        return True
        
    name_lower = company_name.lower()
    obvious_non_business = [
        'talab', 'stp', 'sewage', 'treatment plant',
        'community hall', 'fire station', 'police station',
        'bus stop', 'gate no', 'unnamed road',
        'digital seva csc', 'government service',
        'housing society', 'apartment', 'complex',
        'chhath talav', 'krishna park', 'millenium park',
        'shopping center', 'market', 'mall', 'park', 'garden',
        'playground', 'swimming pool', 'sports complex'
    ]

    for keyword in obvious_non_business:
        if keyword in name_lower:
            return True
    if len(name_lower) < 4:
        return True
    if re.search(r'^\d+\s+[a-z]+\s+(road|street|society|nagar)$', name_lower):
        return True
    return False

def extract_emails_with_selenium(driver, url):
    extracted = set()
    try:
        original_window = driver.current_window_handle
    except:
        return []

    try:
        driver.switch_to.new_window('tab')
        new_window = driver.current_window_handle
        driver.get(url)
        time.sleep(3) 
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        # Extract from homepage first
        page_source = driver.page_source
        
        # Try clicking Company, About Us, Contact Us buttons/links
        button_keywords = [
            ('Company', 'About Us', 'Contact Us', 'Contact', 'About'),
            ('company', 'about us', 'contact us', 'contact', 'about')
        ]
        
        for keyword_set in button_keywords:
            for keyword in keyword_set:
                try:
                    # Try to find button/link with this keyword
                    # Check various element types: button, a, div with onclick, etc.
                    selectors = [
                        f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword.lower()}')]",
                        f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword.lower()}')]",
                        f"//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword.lower()}') and (@onclick or @role='button')]",
                        f"//li[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword.lower()}')]//a"
                    ]
                    
                    for selector in selectors:
                        try:
                            elements = driver.find_elements(By.XPATH, selector)
                            if elements:
                                # Click first matching element
                                elem = elements[0]
                                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                                time.sleep(0.5)
                                try:
                                    elem.click()
                                except:
                                    driver.execute_script("arguments[0].click();", elem)
                                time.sleep(2)  # Wait for content to load
                                
                                # Extract emails from this page
                                page_source += "\n" + driver.page_source
                                break  # Found and clicked, move to next keyword
                        except:
                            continue
                except:
                    continue
        
        # Extract all emails from combined page sources
        patterns = [
            r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
            r"[a-zA-Z0-9_.+-]+\s*\[at\]\s*[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
            r"[a-zA-Z0-9_.+-]+\s*\(at\)\s*[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
            r"/cdn-cgi/l/email-protection#([a-f0-9]+)"  # CloudFlare protected
        ]
        
        for pattern in patterns:
            found = re.findall(pattern, page_source)
            for e in found:
                if pattern.startswith('/cdn-cgi'):
                    # Decode CloudFlare email
                    try:
                        r_val = int(e[:2], 16)
                        decoded = ''.join([chr(int(e[i:i+2], 16) ^ r_val) for i in range(2, len(e), 2)])
                        e = decoded
                    except:
                        continue
                
                e_clean = e.replace('[at]', '@').replace('(at)', '@').replace(' ', '')
                if len(e_clean) > 5 and '.' in e_clean.split('@')[-1]:
                     extracted.add(e_clean.lower())
                     
        # Close the new tab
        if driver.current_window_handle == new_window:
            driver.close()
        
        # Switch back
        driver.switch_to.window(original_window)
        
    except Exception as e:
        # Emergency Cleanup
        try:
            if len(driver.window_handles) > 1:
                for handle in driver.window_handles:
                    if handle != original_window:
                        driver.switch_to.window(handle)
                        driver.close()
            driver.switch_to.window(original_window)
        except:
            pass
             
    return list(extracted)

def extract_company_details(driver, area_name, expected_name=None):
    print("\n" + "="*60)
    
    name = "Not Found"
    name_selectors = [
        "//h1[contains(@class, 'fontHeadlineLarge')]",
        "//h1[contains(@class, 'DUwDvf')]",
        "//div[@role='heading'][@aria-level='1']",
        "//div[contains(@class, 'fontHeadlineLarge')]",
        "//div[contains(@class, 'qBF1Pd')]",
        "//div[contains(@class, 'x3AX1-LfntMc-header-title-title')]"
    ]
    
    for selector in name_selectors:
        try:
            elements = driver.find_elements(By.XPATH, selector)
            for elem in elements:
                if elem.text and len(elem.text.strip()) > 2:
                    name = clean_text(elem.text)
                    break
            if name != "Not Found":
                break
        except:
            continue

    if name == "Not Found":
        logging.warning("Could not extract company name from card")
        return None
        
    if expected_name and expected_name != "Not Found":
        norm_expected = expected_name.lower().replace('pvt', '').replace('ltd', '').replace('company', '').replace('.', '').replace(' ', '')
        norm_actual = name.lower().replace('pvt', '').replace('ltd', '').replace('company', '').replace('.', '').replace(' ', '')
        
        if norm_expected not in norm_actual and norm_actual not in norm_expected:
            import difflib
            ratio = difflib.SequenceMatcher(None, norm_expected, norm_actual).ratio()
            if ratio < 0.6: 
                # Keep mismatch warning for debugging quality
                print(f" MISMATCH WARNING: Expected '{expected_name}' but found '{name}'")
                logging.warning(f"Card Mismatch! Expected: {expected_name}, Found: {name}. Skipping to prevent stale data.")
                return None
    
    if should_skip_company(name):
        print(f" ⏭️ Skipping non-business: {name}")
        logging.info(f"Skipping non-business: {name}")
        return None
    
    print(f"🏢 Company: {name}")
    logging.info(f"Processing company: {name}")
    
    phone = get_phone_number_from_page(driver)
    if phone != "Not Found":
        print(f"   📞 Phone: {phone}")
        logging.info(f"   📞 Phone: {phone}")
    else:
        logging.info("   ⚠️ Phone not found on card")
    
    address = get_address_from_page(driver)
    website = get_website_from_page(driver)
    
    email = "Not Found"
    final_website = "Not Found"
    
    if website != "Not Found":
        if is_social_or_google(website):
            print(f"   ⚠️ Maps website is generic/social ({website}), ignoring...")
            website = "Not Found"
        else:
            print(f"    Using Maps website: {website}")
            logging.info(f"    Using Maps website: {website}")
            final_website = website
            
            print(f"   🔍 Extracting emails from Maps website...")
            emails = extract_emails_from_url(final_website)
            if not emails:
                 print(f" ⚠️ No emails found via requests. Trying Selenium extraction (JS support)...")
                 emails = extract_emails_with_selenium(driver, final_website)
                 
            if emails:
                email = ", ".join(emails)
                print(f" 📧 Found emails: {email}")
            else:
                print(f" ⚠️ No emails found on Maps website")
    
    if final_website == "Not Found":
        print(f" 🔄 Website missing/invalid in Maps, trying VALID BACKUP SEARCH...")
        logging.info(f" 🔄 Starting Backup Search for: {name}")
        backup_website, backup_email = auto_find_website_and_email(name, area_name)
        
        if backup_website != "Not Found":
            final_website = backup_website
            print(f"    Found backup website: {final_website}")
            logging.info(f"    Found backup website: {final_website}")
            
            # If auto_find found email, use it
            if backup_email != "Not Found":
                email = backup_email
                print(f"   📧 Found backup email: {email}")
            else:
                # IMPORTANT: Manually extract email from backup website
                print(f"   🔍 Extracting emails from backup website...")
                extracted_backup_email = extract_emails_from_url(final_website)
                # extract_emails_from_url returns a LIST, convert to string
                if extracted_backup_email and isinstance(extracted_backup_email, list) and len(extracted_backup_email) > 0:
                    email = ", ".join(extracted_backup_email)
                    print(f" 📧 Found emails: {email}")
                else:
                    print(f" ⚠️ No emails found on backup website")
    
    return {
        "Company Name": name,
        "Address": address,
        "Phone (Maps)": phone,
        "Website": final_website,
        "Email (Website)": email
    }

def extract_name_from_card(card):
    # Added Aria-Label extraction to prevent "Not Found" issues
    try:
        # 1. Try ARIA-LABEL on the card div itself
        aria_label = card.get_attribute("aria-label")
        if aria_label and len(aria_label) > 2:
            return clean_text(aria_label)
        
        # 2. Try Link inside (common structure)
        try:
            link = card.find_element(By.TAG_NAME, "a")
            val = link.get_attribute("aria-label")
            if val and len(val) > 2:
                return clean_text(val)
        except:
            pass
        
        # 3. Try Standard HSelectors and Links
        name_elements = card.find_elements(By.XPATH, ".//div[contains(@class, 'fontHeadlineSmall')] | .//div[@role='heading'] | .//a[contains(@class, 'hfpxzc')]")
        for elem in name_elements:
            text = elem.text.strip()
            if not text:
                 text = elem.get_attribute("aria-label")
            
            if text and len(text) > 2:
                return clean_text(text)
        
        # 4. Final Fallback: Check all text in the card via JS (works even if hidden)
        text_content = card.parent.execute_script("return arguments[0].innerText;", card)
        if text_content:
            lines = [l.strip() for l in text_content.split('\n') if len(l.strip()) > 2]
            if lines:
                return clean_text(lines[0])
                
        # 5. SUPER FALLBACK: If we still have nothing, return a generic name so we at least try to click it
        # This prevents the loop from skipping potential valid cards just because we missed the name.
        if text_content and len(text_content) > 5:
             # Just take the first 20 chars
             return clean_text(text_content[:20] + "...")
            
    except Exception as e:
        pass
    return "Not Found"

    return "Not Found"

def scrape_single_area(area_name, target_count, config=None, progress_callback=None):
    
    print(f"\n{'='*60}", flush=True)
    print(f"📍 STARTING AREA: {area_name.upper()}", flush=True)
    print(f" TARGET: {target_count} companies", flush=True)
    print(f"{'='*60}", flush=True)
    logging.info(f"📍 STARTING AREA: {area_name.upper()} (Target: {target_count})")
    
    companies = []
    driver = None
    
    try:
        headless_pref = config.get("HEADLESS", CONFIG["HEADLESS"]) if config else CONFIG["HEADLESS"]
        driver = create_driver(headless=headless_pref)
        if not driver:
            return []
        
        # Search Query
        search_tmpl = config.get("SEARCH_QUERY_TEMPLATE", CONFIG["SEARCH_QUERY_TEMPLATE"]) if config else CONFIG["SEARCH_QUERY_TEMPLATE"]
        search_query = search_tmpl.format(area=area_name)
        encoded_query = requests.utils.quote(search_query)
        maps_url = f"https://www.google.com/maps/search/{encoded_query}"

        print(f"🌐 Opening: {maps_url}")
        driver.get(maps_url)
        time.sleep(5)

        if progress_callback:
            progress_callback({
                "status": "Scrolling",
                "log": f"Scrolling results for {area_name}..."
            })
        
        print(f"📜 Loading companies for {area_name}...", flush=True)
        
        scrollable_div = None
        try:
            scrollable_div = driver.find_element(By.XPATH, "//div[@role='feed']")
        except:
            print("⚠️ Could not find feed element, trying body scroll...")
        
        last_card_count = 0
        same_count_retries = 0
        max_retries = 20  # Increased from 10 for better loading

        while True:
            cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'Nv2PK')]")
            current_count = len(cards)
            
            print(f"   📋 Cards loaded: {current_count} / {target_count}", flush=True)
            if progress_callback and current_count % 20 == 0:
                 progress_callback({"log": f"Loaded {current_count} cards for {area_name}..."})
            
            if current_count >= target_count:
                print(f"    Reached target count!", flush=True)
                break
            
            if current_count == last_card_count:
                same_count_retries += 1
                
                if same_count_retries >= 3:
                     try:
                         if cards:
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", cards[-1])
                            time.sleep(1)
                            try:
                                cards[-1].click()
                            except:
                                driver.execute_script("arguments[0].click();", cards[-1])
                            time.sleep(1)
                            # Return focus to body/list
                            try:
                                driver.find_element(By.TAG_NAME, "body").click()
                            except: pass
                     except: pass
                
                if same_count_retries >= max_retries:
                    print(f"   ⚠️ No new cards found after {max_retries} scrolls. Stopping.", flush=True)
                    break
            else:
                same_count_retries = 0
                last_card_count = current_count
            
            try:
                if scrollable_div:
                    try:
                        ActionChains(driver).move_to_element(scrollable_div).perform()
                        scrollable_div.send_keys(Keys.PAGE_DOWN)
                    except Exception:
                        try:
                            ActionChains(driver).move_to_element(scrollable_div).click().send_keys(Keys.PAGE_DOWN).perform()
                        except Exception:
                            driver.execute_script("arguments[0].scrollTop += 700;", scrollable_div)
                    
                    time.sleep(0.5)
                    try:
                        scrollable_div.send_keys(Keys.END)
                    except:
                        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scrollable_div)

                else:
                    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.END)
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                
                time.sleep(random.uniform(1.5, 2.5))  # Faster scroll loading
                
                # Check for "You've reached the end of the list"
                if "You've reached the end of the list" in driver.page_source:
                    print("    Reached end of list.", flush=True)
                    break
                    
            except Exception as e:
                print(f"   ⚠️ Scroll loop error: {e}", flush=True)
                # Recover by trying to refocus the body
                try: driver.find_element(By.TAG_NAME, "body").click() 
                except: pass
                time.sleep(2)
        
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'Nv2PK')]")
        print(f"\n✅ Total Cards Loaded: {len(cards)}", flush=True)
        
        if len(cards) < 40 and progress_callback:
            warning_msg = (
                "⚠️ LOW RESULTS DETECTED!\n"
                "Please check your internet connection.\n"
                "SUGGESTION: Clear your browser cache/history and run again for maximum results."
            )
            print(f"\n{warning_msg}\n")
            if progress_callback:
                # Custom popup logic for frontend - using 'log' with specific prefix
                progress_callback({
                    "log": f"[POPUP] {warning_msg}",
                    "popup": True, 
                    "duration": 12000 
                })
        
        limit_to_process = min(len(cards), target_count)
        print(f"\n🔄 Processing {limit_to_process} cards...")
        
        # DON'T scroll to top - it closes the list panel!
        # Just start processing cards directly
        
        processed_count = 0
        skipped_count = 0
        seen_companies = set()
        
        for i in range(limit_to_process):
            if len(companies) >= target_count:
                print(f"    ✅ Reached target count of {target_count}!", flush=True)
                break
            
            print(f"\n--- Card {i+1}/{limit_to_process} ---", flush=True)
            
            try:
                # Refresh cards list to avoid stale elements after navigation
                cards_fresh = driver.find_elements(By.XPATH, "//div[contains(@class, 'Nv2PK')]")
                
                # CRITICAL: If list is empty/too small after navigation, wait for reload
                if len(cards_fresh) < limit_to_process and i > 0:
                    print(f"   ⚠️ List shrunk ({len(cards_fresh)} cards). Waiting for reload...", flush=True)
                    retry_count = 0
                    while len(cards_fresh) < limit_to_process and retry_count < 5:
                        time.sleep(2)
                        cards_fresh = driver.find_elements(By.XPATH, "//div[contains(@class, 'Nv2PK')]")
                        retry_count += 1
                    print(f"   ✅ List reloaded: {len(cards_fresh)} cards", flush=True)
                
                if i >= len(cards_fresh):
                    print(f"   ⚠️ Card index {i+1} out of range (list has {len(cards_fresh)} cards). Skipping.")
                    continue
                    
                card = cards_fresh[i]  # Use list index instead of XPath
                
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'auto'});", card)
                    time.sleep(0.5)
                except:
                   pass

                # Verify Content (Is it empty?)
                try:
                    if not card.text.strip():
                        time.sleep(1)
                except: pass

                card_name = extract_name_from_card(card)

                if card_name == "Not Found":
                    try:
                         try:
                             outer_html = card.get_attribute('outerHTML')
                             logging.warning(f"FAILED CARD HTML (Card {i+1}): {outer_html}")
                         except: pass

                         # Force re-fetch of list if element is stale/invalid
                         cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'Nv2PK')]")
                         if i < len(cards):
                             card = cards[i]
                             card_name = extract_name_from_card(card)
                    except:
                        pass
                
                if card_name != "Not Found" and card_name in seen_companies:
                    print(f"   ⏭️ Already processed: {card_name[:30]}...")
                    skipped_count += 1
                    continue
                
                # DON'T try to close buttons before processing!
                # It accidentally closes the search results panel
                
                click_success = False
                max_click_retries = 3
                last_company_name = companies[-1]['Company Name'] if companies else None

                for click_attempt in range(max_click_retries):
                    if click_success:
                        break
                        
                    try:
                        print(f"   🔄 Click attempt {click_attempt + 1}/{max_click_retries}...", flush=True)
                        try:
                            ActionChains(driver).move_to_element(card).perform()
                        except:
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", card)
                        
                        time.sleep(2.0)  # Increased back for reliability

                        try:
                            card.click()
                        except:
                            driver.execute_script("arguments[0].click();", card)
                        
                        start_wait = time.time()
                        details_name = "Not Found"
                        
                        while time.time() - start_wait < 6: # Wait up to 6s
                            try:
                                 # 1. Try Name Verification
                                 verification_selectors = [
                                    "//h1[contains(@class, 'fontHeadlineLarge')]",
                                    "//h1[contains(@class, 'DUwDvf')]",
                                    "//div[@role='heading'][@aria-level='1']",
                                    "//div[contains(@class, 'fontHeadlineLarge')]",
                                    "//h1",
                                    "//div[contains(@class, 'qBF1Pd')]"
                                 ]
                                 
                                 current_candidate = "Not Found"
                                 for v_sel in verification_selectors:
                                     try:
                                         v_elems = driver.find_elements(By.XPATH, v_sel)
                                         if v_elems:
                                             candidate = v_elems[0].text.strip()
                                             if candidate and len(candidate) > 2:
                                                 current_candidate = candidate
                                                 break
                                     except:
                                         continue
                                
                                 if current_candidate != "Not Found":
                                     if last_company_name and (last_company_name.lower() in current_candidate.lower() or current_candidate.lower() in last_company_name.lower()):
                                          time.sleep(0.3)
                                          continue
                                     
                                     details_name = current_candidate
                                     click_success = True
                                     break 
                                 
                            except:
                                pass
                            time.sleep(0.3)
                            
                        # 2. MATCHING LOGIC (If loop finished)
                        if click_success:
                             pass # Already confirmed nice and fresh
                        elif details_name != "Not Found":
                             # We found something, and it wasn't the last company. Accept it.
                             click_success = True
                        
                        if not click_success:
                             # If we failed, maybe we need to click again
                             driver.execute_script("arguments[0].click();", card)
                             time.sleep(1.5)
                        else:
                             break # Exit retry loop
                             
                    except Exception as e:
                        time.sleep(1)

                company_details = None  # Initialize to avoid UnboundLocalError
                
                if not click_success:
                     print(f" Failed to open card for: {card_name}. Processing next...", flush=True)
                     # DO NOT CONTINUE HERE - We must still try to go back/reset state!
                     # continue 
                else: 
                     # Increased delay to ensure data loads properly
                     time.sleep(2.5)  # Reverted for reliability 
                     company_details = extract_company_details(driver, area_name, expected_name=card_name)
                
                if company_details:
                    company_name = company_details['Company Name']
                    
                    # STUCK PANE DETECTION: If the extracted name matches the PREVIOUSLY processed company
                    if len(companies) > 0 and company_name == companies[-1]['Company Name']:
                         print(f"   ⚠️ Stuck on previous company ({company_name}). Retrying card...")
                         pass

                    if company_name in seen_companies:
                        print(f"   ⏭️ Duplicate company skipped: {company_name[:30]}...")
                        skipped_count += 1
                    else:
                        seen_companies.add(company_name)
                        company_data = {
                            "Area": area_name.title(),
                            **company_details
                        }
                        companies.append(company_data)
                        processed_count += 1
                        print(f" {area_name}: {processed_count}. {company_details['Company Name'][:40]}...")

                        # --- PROGRESS UPDATE FOR FRONTEND ---
                        if progress_callback:
                             progress_callback({
                                 "processed": processed_count,
                                 "total": target_count, 
                                 "current_area": area_name
                                 # "log": Removed as per user request (only final notification)
                             })
                        # ------------------------------------
                else:
                    skipped_count += 1
                
                # --- NAVIGATION RESET LOGIC ---
               # CRITICAL: Close detail panel after extraction
                try:
                    # Back button
                    back_btn = driver.find_element(By.XPATH, "//button[@aria-label='Back']")
                    back_btn.click()
                    time.sleep(1.0)
                except:
                    # ESC fallback
                    driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                    time.sleep(0.5)
                                    
            except Exception as e:
                print(f"    Error processing card {i+1}: {str(e)[:50]}")
                # Only use browser back if we are sure we are not on the list page
                # to avoid clearing the search query.
                try:
                    # driver.back() # DISABLE BLIND BACK
                    pass
                except:
                    pass
                continue

        print(f"\n✅ {area_name}: Collected {len(companies)} companies, Skipped {skipped_count}")
        
    except Exception as e:
        print(f" Error scraping {area_name}: {str(e)}")
        import traceback
        traceback.print_exc()
    
    finally:
        if driver:
            try:
                driver.quit()
            except: pass
    
    return companies

def run_scraper(areas, city, category="it", custom_query="", progress_callback=None):
    print("\n" + "="*60, flush=True)
    print("🏢 SURAT IT COMPANIES SCRAPER - INTEGRATED MODE", flush=True)
    print("="*60, flush=True)
    
    all_companies = []
    
    # Get valid areas
    valid_areas = [area.strip() for area in areas if area.strip()]
    
    if not valid_areas:
        print("⚠️ No valid areas provided!")
        return None
    
    # Prepare configs for each area
    area_tasks = []
    for area in valid_areas:
        config = CONFIG.copy()
        if custom_query:
            config["SEARCH_QUERY_TEMPLATE"] = custom_query
        else:
            if category == "it":
                config["SEARCH_QUERY_TEMPLATE"] = f"IT companies in {{area}} {city}"
            else:
                config["SEARCH_QUERY_TEMPLATE"] = f"{category} in {{area}} {city}"
        
        # Use CONFIG target (fallback uses global variable, so only change CONFIG!)
        target = config.get("TARGET_PER_AREA_MIN", TARGET_PER_AREA_MIN)
        area_tasks.append((area, target, config))
    
    # === PARALLEL PROCESSING ===
    max_workers = min(CONFIG.get("BROWSER_INSTANCES", 2), len(valid_areas))
    print(f"\n🚀 Starting {max_workers} parallel browser(s) for {len(valid_areas)} area(s)...\n", flush=True)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_area = {
            executor.submit(scrape_single_area, area, target, config, progress_callback): area
            for area, target, config in area_tasks
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_area):
            area_name = future_to_area[future]
            try:
                area_results = future.result()
                if area_results:
                    all_companies.extend(area_results)
                    print(f"\n✅ {area_name}: Successfully collected {len(area_results)} companies\n", flush=True)
                else:
                    print(f"\n⚠️ {area_name}: No results collected\n", flush=True)
            except Exception as e:
                print(f"\n❌ {area_name}: Error occurred - {str(e)[:100]}\n", flush=True)
                import traceback
                traceback.print_exc()
             
    if all_companies:
        filename = save_to_excel_with_backup(all_companies)
        
        # --- FINAL COMPLETION NOTIFICATION ---
        if progress_callback:
            progress_callback({
                "log": f"[POPUP] ✅ Scraping Completed! File ready Please Download: {os.path.basename(filename)}",
                "popup": True,
                "duration": 20000, # 20 seconds
                "status": "Completed"
            })
        # -------------------------------------
        
        return filename
    
    return None

def save_to_excel_with_backup(all_data, filename=None):
    if not filename:
        date_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
        base_name = f"Surat_data_{date_str}" 
        filename = f"{base_name}.xlsx"
    
    df = pd.DataFrame(all_data, columns=["Area", "Company Name", "Address", "Phone (Maps)", "Website", "Email (Website)"])
    df["Address"] = df["Address"].apply(clean_address)
    df["Company Name"] = df["Company Name"].apply(clean_text)
    
    df_clean = df.drop_duplicates(subset=['Company Name', 'Address'], keep='first')
    df_clean.to_excel(filename, index=False)
    
    try:
        from openpyxl import load_workbook
        wb = load_workbook(filename)
        ws = wb.active
        for column in ws.columns:
            max_length = 0
            column = [cell for cell in column]
            column_letter = column[0].column_letter
            
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            
            # MOBILE-FRIENDLY: Reduce column widths
            # Set maximum widths for better mobile viewing
            if column_letter == 'C':  # Address column
                adjusted_width = min(max_length + 2, 35)
            elif column_letter == 'B':  # Company Name column
                adjusted_width = min(max_length + 2, 30)
            else:
                adjusted_width = min(max_length + 2, 40)  # Max 40 for all others
            
            ws.column_dimensions[column_letter].width = adjusted_width
        wb.save(filename)
    except:
        pass
        
    print(f"\n💾 SAVED TO: {filename}")
    return filename

if __name__ == "__main__":
    scrape_single_area("Adajan", 5)

