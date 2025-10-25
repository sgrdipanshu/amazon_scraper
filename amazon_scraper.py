#!/usr/bin/env python3
# amazon_scraper.py
# Full-fledged Amazon.in product scraper:
# - Captures all requested PDP fields
# - JSON-first gallery (robust), hover/click fallback
# - Canonical m.media-amazon.com image links with chosen _SL{size}_

import re, json, time, argparse, io, os
from contextlib import suppress
from typing import List, Dict, Any, Tuple

import requests
from PIL import Image
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# -------------------- Config --------------------
DEFAULT_INPUT = "asins.xlsx"
DEFAULT_OUT_LONG = "amazon_full_product_data.xlsx"

AMZ_IMG_HOSTS = (
    "m.media-amazon.com",
    "images-na.ssl-images-amazon.com",
    "images-eu.ssl-images-amazon.com",
)

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Connection": "keep-alive",
}
TIMEOUT_S = 25

# Will be set from CLI
FINAL_SL = 1200          # final canonical _SL{size}_ for m.media URLs
TARGET_PX = 0            # optional probe target for biggest real pixels (0 = disabled)
SAVE_IMAGES = False
IMAGES_DIR = "images"

# -------------------- Small utils --------------------
def is_amz_img(u: str) -> bool:
    return u and u.startswith("https://") and any(h in u for h in AMZ_IMG_HOSTS)

def soupify(driver) -> BeautifulSoup:
    return BeautifulSoup(driver.page_source, "lxml")

def safe_text(el) -> str:
    return el.get_text(strip=True) if el else ""

def first_text(soup: BeautifulSoup, selectors: List[str]) -> str:
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            t = safe_text(el)
            if t:
                return t
    return ""

# -------------------- Canonical image URL helpers --------------------
import urllib.parse as _urlparse

def _strip_size_token(u: str) -> str:
    # remove tokens like ._SL1500_.  ._SX466_.  ._SR300,300_.  ._QL70_. etc.
    return re.sub(r'\._[^_.]+_\.', '.', u)

def _force_sl_token_path(path: str, size: int) -> str:
    # ensure ._SL{size}_. exists (and replace any other tokens) on PATH (not full URL)
    p = _strip_size_token(path)
    # inject before extension if missing
    p = re.sub(r'(\.(jpg|jpeg|png|webp))($|\?)', rf'._SL{size}_.\1', p, flags=re.I)
    # de-dup just in case
    p = p.replace(f'._SL{size}__SL{size}_.', f'._SL{size}_.')
    return p

def to_mmedia_amazon(u: str, size: int) -> str:
    """
    Canonicalize ANY Amazon image URL to:
    https://m.media-amazon.com/images/I/<KEY>._SL{size}_.jpg
    """
    if not u or "/images/I/" not in u:
        return u
    u = _strip_size_token(u.split("?")[0])
    p = _urlparse.urlparse(u)
    path = p.path
    m = re.search(r'(/images/I/[^?]+?\.(jpg|jpeg|png|webp))$', path, re.I)
    if not m:
        return u
    path = m.group(1)
    path = re.sub(r'\.(jpg|jpeg|png|webp)$', '.jpg', path, flags=re.I)
    path = _force_sl_token_path(path, size)
    return _urlparse.urlunparse(('https', 'm.media-amazon.com', path, '', '', ''))

# -------------------- Optional HQ verification --------------------
def strip_size_token_full(u: str) -> str:
    return _strip_size_token(u.split("?")[0])

def force_sl_full(u: str, size: int) -> str:
    v = strip_size_token_full(u)
    v = re.sub(r'(\.(jpg|jpeg|png|webp))($|\?)', rf'._SL{size}_.\1', v, flags=re.I)
    return v.replace(f'._SL{size}__SL{size}_.', f'._SL{size}_.')  # de-dup

def candidate_urls(u: str) -> List[str]:
    base = strip_size_token_full(u)
    sizes = [4096, 3600, 3000, 2400, 2000, 1500]
    cands = [base] + [force_sl_full(base, s) for s in sizes]
    out, seen = [], set()
    for c in cands:
        if is_amz_img(c) and c not in seen:
            seen.add(c); out.append(c)
    return out

def fetch_image_size(url: str) -> Tuple[int, int]:
    r = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT_S)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content))
    im.load()
    return im.width, im.height

def choose_hq_url(u: str, target_px: int) -> str:
    """
    If target_px>0, probe Original/SL4096→… and pick the first meeting target.
    Otherwise just return u as-is.
    """
    if target_px <= 0:
        return u
    best_url, best_area = u, 0
    for cand in candidate_urls(u):
        try:
            w, h = fetch_image_size(cand)
            area = w * h
            if area > best_area:
                best_url, best_area = cand, area
            if max(w, h) >= target_px:
                return cand
        except Exception:
            continue
    return best_url

def save_images_locally(asin: str, urls: List[str], root: str):
    d = os.path.join(root, asin)
    os.makedirs(d, exist_ok=True)
    for idx, u in enumerate(urls, 1):
        try:
            resp = requests.get(u, headers=HTTP_HEADERS, timeout=TIMEOUT_S)
            resp.raise_for_status()
            ext = ".jpg"
            m = re.search(r'\.(jpg|jpeg|png|webp)($|\?)', u, re.I)
            if m: ext = f".{m.group(1).lower()}"
            path = os.path.join(d, f"image_{idx}{ext}")
            with open(path, "wb") as f:
                f.write(resp.content)
        except Exception:
            continue

# -------------------- Selenium driver & page prep --------------------
def make_driver(headless=True):
    opts = Options()
    if headless: opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-features=AutomationControlled")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--lang=en-IN")
    opts.add_argument("accept-language=en-IN,en;q=0.9")
    opts.add_argument(HTTP_HEADERS["User-Agent"])
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    service = Service(ChromeDriverManager().install())
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(45)
    return drv

def dismiss_popups(driver, timeout=5):
    with suppress(Exception):
        WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input#sp-cc-accept, button[aria-label='Close']"))
        )
    for sel in ["input#sp-cc-accept", "button[aria-label='Close']", "button[data-action='a-popover-close']"]:
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            with suppress(Exception):
                el.click(); time.sleep(0.2)

# -------------------- Gallery discovery --------------------
RAIL_CONTAINERS = [
    "#altImages", "#imageBlockThumbs", "div#leftCol #altImages",
    "[data-csa-c-content-id='imageBlock'] #altImages", "#imageBlock", "#imageBlock_feature_div"
]
THUMB_QUERY = (
    "img.imageThumbnail, "
    "#altImages li.imageThumbnail img, "
    "#altImages ul.a-unordered-list li img, "
    "#imageBlockThumbs img, "
    "li[data-csa-c-type='image-block-image'] img, "
    "button[aria-label*='image'] img"
)

def get_visible_thumb_srcs(soup: BeautifulSoup) -> List[str]:
    urls, seen = [], set()
    thumbs = soup.select(THUMB_QUERY)
    for img in thumbs:
        dyn = img.get("data-a-dynamic-image")
        if dyn:
            with suppress(Exception):
                dd = json.loads(dyn)
                for k in dd.keys():
                    if is_amz_img(k):
                        u = strip_size_token_full(k)
                        if "/images/I/" in u and u not in seen:
                            seen.add(u); urls.append(u)
        for a in ("src","data-src","data-old-hires"):
            s = img.get(a)
            if s and is_amz_img(s):
                u = strip_size_token_full(s)
                if "/images/I/" in u and u not in seen:
                    seen.add(u); urls.append(u)
        srcset = img.get("srcset")
        if srcset:
            cand = srcset.split(",")[0].strip().split(" ")[0]
            if cand and is_amz_img(cand):
                u = strip_size_token_full(cand)
                if "/images/I/" in u and u not in seen:
                    seen.add(u); urls.append(u)
    urls = [u for u in urls if not any(x in u for x in (".svg","sprite","play-button",".gif"))]
    return list(dict.fromkeys(urls))

def harvest_from_imageblock_jsons(soup: BeautifulSoup) -> List[str]:
    out, seen = [], set()
    # ImageBlockATF
    for sc in soup.find_all("script"):
        st = sc.string or ""
        if "ImageBlockATF" in st:
            m = re.search(r'P\.register\("ImageBlockATF",\s*(\{.*?\})\s*\);', st, re.S)
            if m:
                with suppress(Exception):
                    data = json.loads(m.group(1))
                    imgs = data.get("colorImages", {}).get("initial", [])
                    for it in imgs:
                        tp = (it.get("type") or it.get("variant") or "IMAGE").upper()
                        if any(t in tp for t in ("VIDEO","SPIN","360")):
                            continue
                        for k in ("hiRes","zoomed","superUrl","mainUrl","large","main","url"):
                            v = it.get(k)
                            if v and is_amz_img(v):
                                u = strip_size_token_full(v)
                                if "/images/I/" in u and u not in seen:
                                    seen.add(u); out.append(u)
            break
    # imageGalleryData
    for sc in soup.find_all("script"):
        st = sc.string or ""
        if "imageGalleryData" in st:
            m = re.search(r'"imageGalleryData"\s*:\s*(\[[^\]]+\])', st, re.S)
            if m:
                with suppress(Exception):
                    arr = json.loads(m.group(1))
                    for node in arr:
                        ntype = (node.get("type") or node.get("mediaType") or "IMAGE").upper()
                        if any(t in ntype for t in ("VIDEO","SPIN","360")):
                            continue
                        for k in ("hiRes","zoomed","superUrl","mainUrl","large","main","url"):
                            v = node.get(k)
                            if v and is_amz_img(v):
                                u = strip_size_token_full(v)
                                if "/images/I/" in u and u not in seen:
                                    seen.add(u); out.append(u)
                        for var in node.get("variants", []):
                            vtype = (var.get("type") or "IMAGE").upper()
                            if any(t in vtype for t in ("VIDEO","SPIN","360")):
                                continue
                            for k in ("hiRes","zoomed","superUrl","url","large","main"):
                                v = var.get(k)
                                if v and is_amz_img(v):
                                    u = strip_size_token_full(v)
                                    if "/images/I/" in u and u not in seen:
                                        seen.add(u); out.append(u)
            break
    out = [u for u in out if "/images/I/" in u and not any(x in u for x in (".svg","sprite","play-button",".gif"))]
    return list(dict.fromkeys(out))

def hover_collect(driver) -> List[str]:
    out, seen = [], set()
    with suppress(Exception):
        WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, ",".join(RAIL_CONTAINERS))))
    actions = ActionChains(driver)
    thumbs = driver.find_elements(By.CSS_SELECTOR, THUMB_QUERY)

    for t in thumbs:
        with suppress(Exception):
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
            actions.move_to_element(t).pause(0.06).perform()
            driver.execute_script("""
                ['mouseover','mouseenter','mousemove'].forEach(ev =>
                    arguments[0].dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true}))
                );
            """, t)
            time.sleep(0.15)
        s2 = soupify(driver)
        li2 = s2.find("img", id="landingImage") or s2.select_one("#imgTagWrapperId img")
        if li2:
            taken = False
            dyn = li2.get("data-a-dynamic-image")
            if dyn:
                with suppress(Exception):
                    dd = json.loads(dyn)
                    for k in dd.keys():
                        if is_amz_img(k) and "/images/I/" in k:
                            u = strip_size_token_full(k)
                            if u not in seen:
                                seen.add(u); out.append(u); taken = True
            if not taken:
                for a in ("src","data-old-hires","data-src"):
                    src = li2.get(a)
                    if src and is_amz_img(src) and "/images/I/" in src:
                        u = strip_size_token_full(src)
                        if u not in seen:
                            seen.add(u); out.append(u)
                        break
    return list(dict.fromkeys(out))

def click_collect(driver) -> List[str]:
    out, seen = [], set()
    with suppress(Exception):
        WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, ",".join(RAIL_CONTAINERS))))
    thumbs = driver.find_elements(By.CSS_SELECTOR, THUMB_QUERY)

    for t in thumbs:
        with suppress(Exception):
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
            t.click(); time.sleep(0.12)
        s2 = soupify(driver)
        li2 = s2.find("img", id="landingImage") or s2.select_one("#imgTagWrapperId img")
        if li2:
            taken = False
            dyn = li2.get("data-a-dynamic-image")
            if dyn:
                with suppress(Exception):
                    dd = json.loads(dyn)
                    for k in dd.keys():
                        if is_amz_img(k) and "/images/I/" in k:
                            u = strip_size_token_full(k)
                            if u not in seen:
                                seen.add(u); out.append(u); taken = True
            if not taken:
                for a in ("src","data-old-hires","data-src"):
                    src = li2.get(a)
                    if src and is_amz_img(src) and "/images/I/" in src:
                        u = strip_size_token_full(src)
                        if u not in seen:
                            seen.add(u); out.append(u)
                        break
    return list(dict.fromkeys(out))

# -------------------- PDP parsing (fields) --------------------
def parse_bullets(soup: BeautifulSoup) -> List[str]:
    bullets = [b.get_text(strip=True) for b in soup.select("#feature-bullets ul li span.a-list-item") if b.get_text(strip=True)]
    while len(bullets) < 5:
        bullets.append("")
    return bullets[:5]

def parse_description(soup: BeautifulSoup) -> str:
    desc = soup.select_one("#productDescription, #productDescription_feature_div")
    if desc and desc.get_text(strip=True):
        return desc.get_text(" ", strip=True)
    ap = soup.select_one("#aplus, .aplus, .aplus-module-wrapper")
    return ap.get_text(" ", strip=True) if ap else ""

def parse_pricing(soup: BeautifulSoup) -> Dict[str, str]:
    out = {"MRP":"", "Selling_Price":"", "Deal_Name":""}
    mrp_tag = soup.select_one("span.a-text-strike, span.priceBlockStrikePriceString")
    if mrp_tag: out["MRP"] = mrp_tag.get_text(strip=True)
    for sel in ["#corePriceDisplay_desktop_feature_div .a-price .a-offscreen", "#pdp-ipr .a-price .a-offscreen",
                "#priceblock_dealprice", "#priceblock_ourprice", "#priceblock_saleprice", ".a-price .a-offscreen"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            out["Selling_Price"] = el.get_text(strip=True); break
    out["Deal_Name"] = first_text(soup, ["#dealBadge span", "span.dealBadgeText", "#dealBadgeBadgeType", "#dealPriceBadge", ".savingsPercentage"])
    return out

def parse_ebc_video(soup: BeautifulSoup) -> Dict[str, str]:
    ebc = "Yes" if soup.select_one("#aplus, .aplus, .aplus-module-wrapper") else "No"
    has_video = (
        bool(soup.select_one("#video-block, #ivImagesTab, #videoGallery, iframe[src*='amazon']"))
        or bool(re.search(r'\"hasVideo\"\s*:\s*true', str(soup), re.I))
    )
    return {"EBC_Content": ebc, "Has_Video": "Yes" if has_video else "No"}

def parse_technical_details(soup: BeautifulSoup) -> Dict[str, str]:
    tech = {}
    for row in soup.select("#productDetails_techSpec_section_1 tr, .prodDetTable tr, #productDetails_detailBullets_sections1 tr"):
        key = safe_text(row.find("th") or row.find("td", class_="label"))
        val = safe_text(row.find("td", class_="value") or row.find("td"))
        key = key.strip().strip(":").replace("\u200f","")
        val = val.strip().replace("\u200f","")
        if key and val:
            tech[key] = val
    return tech

def parse_whats_in_box(soup: BeautifulSoup, bullets: List[str]) -> str:
    for row in soup.select("#productDetails_detailBullets_sections1 tr, #productDetails_techSpec_section_1 tr, .prodDetTable tr"):
        key = safe_text(row.find("th") or row.find("td", class_="label")).lower()
        val = safe_text(row.find("td", class_="value") or row.find("td"))
        if key and ("in the box" in key or "included" in key):
            return val
    for b in bullets:
        lb = b.lower()
        if "in the box" in lb or lb.startswith("includes"):
            return b
    return ""

def parse_reviews_block(soup: BeautifulSoup) -> Dict[str, str]:
    rc = soup.select_one("#acrCustomerReviewText")
    ar = soup.select_one("span.a-icon-alt")
    q = soup.select_one("#askATFLink span, #askATFLink")
    return {
        "Review_Count": rc.get_text(strip=True) if rc else "",
        "Average_Rating": ar.get_text(strip=True) if ar else "",
        "Questions_Count": safe_text(q) if q else ""
    }

def parse_bsr(soup: BeautifulSoup, page_html: str) -> str:
    for th in soup.find_all("th"):
        if "Best Sellers Rank" in th.get_text(strip=True):
            td = th.find_next_sibling("td")
            if td:
                return td.get_text(" ", strip=True)
    m = re.search(r'#\d[\d,]* in [^<\n]+', page_html)
    return m.group(0) if m else ""

def parse_seller(soup: BeautifulSoup) -> str:
    seller_el = soup.find(id="sellerProfileTriggerId")
    return safe_text(seller_el) if seller_el else first_text(soup, ["#bylineInfo", "a#bylineInfo"])

def parse_variations_from_scripts(soup: BeautifulSoup) -> Dict[str, Any]:
    out = {}
    for sc in soup.find_all("script"):
        st = sc.string or ""
        if not st:
            continue
        if "variationValues" in st:
            with suppress(Exception):
                m = re.search(r'var\s+data\s*=\s*(\{.*?"variationValues".*?\})\s*;', st, re.S)
                if m:
                    d = json.loads(m.group(1)); out.update(d.get("variationValues", {}))
        if "dimensionValuesDisplayData" in st:
            with suppress(Exception):
                m = re.search(r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})', st, re.S)
                if m:
                    d = json.loads(m.group(1)); out.update(d)
        if "twister-js-init-dpx-data" in st:
            with suppress(Exception):
                m = re.search(r'"twister-js-init-dpx-data"\s*:\s*(\{.*?\})', st, re.S)
                if m:
                    d = json.loads(m.group(1)); out.update(d)
    return out

# -------------------- Core scraping --------------------
def make_pdp_driver():
    return make_driver(headless=True)

def scrape_product(asin: str, allow_hover=False, thorough=False) -> Dict[str, Any]:
    out = {
        "ASIN": asin,
        "Brand": "", "Title": "",
        "Bullet_1": "", "Bullet_2": "", "Bullet_3": "", "Bullet_4": "", "Bullet_5": "",
        "Description": "",
        "MRP": "", "Selling_Price": "", "Deal_Name": "",
        "EBC_Content": "No", "Has_Video": "No",
        "Technical_Details": {}, "Whats_in_the_Box": "",
        "Review_Count": "", "Average_Rating": "", "Questions_Count": "",
        "Best_Sellers_Rank": "", "Seller": "",
        "Variation_Data": {},
        "Images": [], "Image_Count": 0,
        "Status": "Success", "Error_Message": ""
    }

    driver = None
    try:
        driver = make_pdp_driver()
        driver.get(f"https://www.amazon.in/dp/{asin}")
        dismiss_popups(driver)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#productTitle, #title")))

        soup = soupify(driver)

        # Title & Brand
        out["Title"] = first_text(soup, ["#productTitle", "#title #productTitle"])
        out["Brand"] = first_text(soup, ["#bylineInfo", "a#bylineInfo"])

        # Bullets
        b1, b2, b3, b4, b5 = parse_bullets(soup)
        out["Bullet_1"], out["Bullet_2"], out["Bullet_3"], out["Bullet_4"], out["Bullet_5"] = b1, b2, b3, b4, b5

        # Description / EBC / Video
        out["Description"] = parse_description(soup)
        out.update(parse_ebc_video(soup))

        # Pricing / Deal
        pricing = parse_pricing(soup)
        out["MRP"], out["Selling_Price"], out["Deal_Name"] = pricing["MRP"], pricing["Selling_Price"], pricing["Deal_Name"]

        # Tech details / Box contents
        tech = parse_technical_details(soup)
        out["Technical_Details"] = tech
        out["Whats_in_the_Box"] = parse_whats_in_box(soup, [b1,b2,b3,b4,b5])

        # Reviews / Q&A
        out.update(parse_reviews_block(soup))

        # BSR / Seller
        out["Best_Sellers_Rank"] = parse_bsr(soup, driver.page_source)
        out["Seller"] = parse_seller(soup)

        # Variations
        out["Variation_Data"] = parse_variations_from_scripts(soup)

        # Images: JSON-first
        rail_thumb_urls = get_visible_thumb_srcs(soup)
        json_gallery_urls = harvest_from_imageblock_jsons(soup)

        allowed = rail_thumb_urls if rail_thumb_urls else json_gallery_urls
        if rail_thumb_urls and json_gallery_urls:
            allowed = [u for u in json_gallery_urls if u in set(rail_thumb_urls)]

        if allow_hover and not allowed:
            # try UI interactions to coax images in odd layouts
            allowed = hover_collect(driver)
        if thorough and not allowed:
            allowed = click_collect(driver)

        base_urls = list(dict.fromkeys(allowed))

        # Optional HQ probe (if TARGET_PX > 0) then canonicalize to m.media SL
        upgraded = []
        for u in base_urls:
            u2 = choose_hq_url(u, TARGET_PX) if TARGET_PX > 0 else u
            upgraded.append(to_mmedia_amazon(u2, FINAL_SL))

        out["Images"] = list(dict.fromkeys(upgraded))
        out["Image_Count"] = len(out["Images"])

        # Optionally save images
        if SAVE_IMAGES and out["Images"]:
            save_images_locally(asin, out["Images"], IMAGES_DIR)

    except Exception as e:
        out["Status"] = "Error"
        out["Error_Message"] = str(e)
    finally:
        with suppress(Exception):
            if driver: driver.quit()
    return out

# -------------------- Output shaping --------------------
COLUMNS = [
    "ASIN","Brand","Title",
    "Bullet_1","Bullet_2","Bullet_3","Bullet_4","Bullet_5",
    "Description","MRP","Selling_Price","Deal_Name",
    "EBC_Content","Has_Video",
    "Technical_Details","Whats_in_the_Box",
    "Review_Count","Average_Rating","Questions_Count","Best_Sellers_Rank",
    "Seller","Variation_Data",
    "Image_Index","Image_URL_1500px","Image_Count","Status","Error_Message"
]

def to_long_rows(prod: Dict[str, Any]) -> List[Dict[str, Any]]:
    base = {
        "ASIN": prod["ASIN"],
        "Brand": prod["Brand"],
        "Title": prod["Title"],
        "Bullet_1": prod["Bullet_1"], "Bullet_2": prod["Bullet_2"],
        "Bullet_3": prod["Bullet_3"], "Bullet_4": prod["Bullet_4"], "Bullet_5": prod["Bullet_5"],
        "Description": prod["Description"],
        "MRP": prod["MRP"], "Selling_Price": prod["Selling_Price"], "Deal_Name": prod["Deal_Name"],
        "EBC_Content": prod["EBC_Content"], "Has_Video": prod["Has_Video"],
        "Technical_Details": json.dumps(prod["Technical_Details"], ensure_ascii=False, sort_keys=True),
        "Whats_in_the_Box": prod["Whats_in_the_Box"],
        "Review_Count": prod["Review_Count"], "Average_Rating": prod["Average_Rating"],
        "Questions_Count": prod["Questions_Count"], "Best_Sellers_Rank": prod["Best_Sellers_Rank"],
        "Seller": prod["Seller"], "Variation_Data": json.dumps(prod["Variation_Data"], ensure_ascii=False, sort_keys=True),
        "Image_Count": prod["Image_Count"], "Status": prod["Status"], "Error_Message": prod["Error_Message"]
    }
    rows = []
    if prod["Images"]:
        for i, url in enumerate(prod["Images"], start=1):
            r = dict(base)
            # keep requested column name "Image_URL_1500px" even though size is configurable
            r.update({"Image_Index": i, "Image_URL_1500px": url})
            rows.append(r)
    else:
        r = dict(base); r.update({"Image_Index": 0, "Image_URL_1500px": ""})
        rows.append(r)
    # exact column order
    return [{k: row.get(k, "") for k in COLUMNS} for row in rows]

# -------------------- Main --------------------
def main():
    ap = argparse.ArgumentParser(description="Amazon.in full PDP scraper (canonical m.media URLs, selectable SL size)")
    ap.add_argument("-i","--input", default=DEFAULT_INPUT, help="Input Excel with column 'ASIN'")
    ap.add_argument("--out", default=DEFAULT_OUT_LONG, help="Output Excel filename (long format)")
    ap.add_argument("--sl", type=int, default=1200, help="Final SL size for image URLs (e.g., 1200, 1500, 2000, 2400)")
    ap.add_argument("--target-px", type=int, default=0, help="(Optional) probe for real image >= this px (0 disables probing)")
    ap.add_argument("--allow-hover", action="store_true", help="Try a hover pass if JSON yields 0 images")
    ap.add_argument("--thorough", action="store_true", help="Also try a click pass if needed (slower)")
    ap.add_argument("--save-images", action="store_true", help="Download images to disk for QC")
    ap.add_argument("--images-dir", default="images", help="Folder for --save-images")
    ap.add_argument("--delay-ms", type=int, default=400, help="Delay between ASINs (ms)")
    ap.add_argument("--retries", type=int, default=1, help="Retries on failure or 0 images")
    args = ap.parse_args()

    global FINAL_SL, TARGET_PX, SAVE_IMAGES, IMAGES_DIR
    FINAL_SL = max(200, args.sl)
    TARGET_PX = max(0, args.target_px)
    SAVE_IMAGES = bool(args.save_images)
    IMAGES_DIR = args.images_dir

    try:
        df_in = pd.read_excel(args.input)
    except Exception as e:
        print(f"Failed to read {args.input}: {e}")
        return
    if "ASIN" not in df_in.columns:
        print("Input Excel must contain an 'ASIN' column.")
        return

    asins = [str(x) for x in df_in["ASIN"].dropna().astype(str).tolist()]
    print(f"Total ASINs: {len(asins)}")

    all_rows: List[Dict[str, Any]] = []
    for idx, asin in enumerate(asins, start=1):
        print(f"[{idx}/{len(asins)}] {asin} …")
        prod = scrape_product(asin, allow_hover=args.allow_hover, thorough=args.thorough)

        tries = 0
        while (prod["Status"] != "Success" or prod["Image_Count"] == 0) and tries < args.retries:
            tries += 1
            print(f"  -> retry {tries} …")
            time.sleep(1.0)
            prod = scrape_product(asin, allow_hover=True, thorough=True)

        if prod["Status"] == "Success":
            print(f"  ✓ {asin}: {prod['Image_Count']} image(s)")
        else:
            print(f"  ✗ {asin}: ERROR {prod['Error_Message']}")

        all_rows.extend(to_long_rows(prod))
        time.sleep(max(0, args.delay_ms) / 1000.0)

    df_out = pd.DataFrame(all_rows, columns=COLUMNS)
    df_out.to_excel(args.out, index=False, engine="openpyxl")
    print(f"Saved: {args.out}")

if __name__ == "__main__":
    main()
