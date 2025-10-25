<h1 align="center">🛒 Amazon Product Scraper (India)</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.x-blue?logo=python" alt="Python Version">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Automation-Selenium-orange?logo=selenium" alt="Selenium">
  <img src="https://img.shields.io/github/last-commit/yourusername/amazon-product-scraper" alt="Last Commit">
</p>

<p align="center">
  <img src="https://github.com/yourusername/amazon-product-scraper/assets/banner.png" alt="Amazon Product Scraper" width="80%">
</p>

---

## 🚀 Overview

**Amazon Product Scraper (India)** is a **powerful automated tool** built with **Selenium**, **BeautifulSoup**, and **Pandas**, capable of extracting complete product information and **high-resolution 1500px images** directly from Amazon.in.

This scraper can handle **large ASIN datasets** efficiently and exports all structured data to a neatly formatted Excel sheet.

---

## ✨ Features

✅ Extracts complete product details:

- 🏷️ **Title** and **Brand**
- 💰 **MRP**, **Selling Price**, and **Deal Name**
- 📋 **Top 5 Bullet Points**
- 🧾 **Product Description**
- ⚙️ **Technical Details**
- 📦 **What's in the Box**
- ⭐ **Ratings**, **Reviews Count**, and **Questions Count**
- 🏆 **Best Seller Rank** & **Seller Name**
- 🖼️ **All Product Images (1500px)** from the left-side gallery  
- 📊 **Image Count (1–7)**  
- 🎥 **EBC (A+ Content)** and **Video Presence** indicators  

✅ Reads ASINs directly from Excel  
✅ Fully **headless browser** (no Chrome window needed)  
✅ Exports all results into an Excel file: `amazon_full_product_data.xlsx`  
✅ Prevents duplicate image entries and ensures all images are 1500px  

---

## 🧩 Tech Stack

| Component | Description |
|------------|--------------|
| **Language** | Python 3.x |
| **Core Libraries** | Selenium, BeautifulSoup4, Pandas, OpenPyXL |
| **Driver Manager** | Webdriver-Manager (auto-installs ChromeDriver) |
| **Output** | Excel (.xlsx) |
| **Browser** | Chrome (Headless Mode) |

---

## ⚙️ Setup Guide

### 1️⃣ Clone the Repository

```bash
git clone https://github.com/yourusername/amazon-product-scraper.git
cd amazon-product-scraper
2️⃣ Install Dependencies
bash
Copy code
pip install selenium beautifulsoup4 pandas openpyxl webdriver-manager
3️⃣ Add ASINs in Excel
Create a file named asins.xlsx in the root folder with a column header ASIN:

ASIN
B084685MT1
B0CQZ4C9B7
B0DVZ77NRC

▶️ Run the Scraper
bash
Copy code
python amazon_scraper.py
The scraper will:

Read ASINs from asins.xlsx

Scrape product details and all available images (1–7, 1500px)

Save everything to amazon_full_product_data.xlsx

🧾 Output Example
ASIN	Title	Brand	MRP	Selling_Price	Image_Count	Image_URL_1500px	Description	...
B084685MT1	Philips Trimmer	Philips	₹2,495	₹1,899	5	https://m.media-amazon.com/images/I/..._SL1500_.jpg	Grooming essential...	...

⚠️ Notes
This project is intended only for educational and research purposes.

Do not use it for mass scraping or violate Amazon’s Terms of Service.

Use time delays for large ASIN batches to avoid temporary bans.

Amazon may change its layout — you may need to update selectors.

🧑‍💻 Author
👨‍💻 Dipanshu Sagar
Developer • Automation Enthusiast • Data Engineer

📧 [dsagartp@gmail.com]
🌐 https://github.com/sgrdipanshu

🏷️ License
This project is licensed under the MIT License.
You are free to modify, distribute, and use it with attribution.
