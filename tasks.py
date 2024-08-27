from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException
from robocorp import workitems, vault
from robocorp.tasks import task
from RPA.Excel.Files import Files as Excel
from RPA.core.webdriver import start, download

import os
from pathlib import Path
import requests
from datetime import datetime
import requests
import re
import logging
from dateutil.relativedelta import relativedelta
from boto3 import client



aws = vault.get_secret("AWS")
os.environ["AWS_ACCESS_KEY_ID"] = aws["AWS_ACCESS_KEY"]
os.environ["AWS_SECRET_ACCESS_KEY"] = aws["AWS_SECRET_ACCESS"]
os.environ["AWS_DEFAULT_REGION"] = aws["AWS_REGION"]



OUTPUT_DIR = Path(os.getenv("ROBOT_ARTIFACTS", "output"))


class Crawler:
    def __init__(self, url, search_term, num_months, category):
        self.url = url
        self.search_term = search_term
        self.num_months = num_months
        self.category = category
        self.target_date = self._get_target_date()
        self.s3 = client("s3")
        self.logger = logging.getLogger(__name__)
        self.driver = None

    def create_workbook(self, file_path):
        self.excel = Excel()
        self.excel_path = OUTPUT_DIR / file_path
        self.excel.create_workbook(self.excel_path)

    def _get_target_date(self):
        target_date = datetime.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        if self.num_months == 2:
            target_date = (datetime.now() - relativedelta(months=1)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        elif self.num_months == 3:
            target_date = (datetime.now() - relativedelta(months=2)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        
        return target_date

    def set_chrome_options(self):
        options = webdriver.ChromeOptions()
        options.add_argument('--no-sandbox')
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument('--disable-web-security')
        options.add_argument("--start-maximized")
        options.add_argument('--remote-debugging-port=9222')
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        return options

    def set_webdriver(self, browser="Chrome"):
        options = self.set_chrome_options()
        self.driver = start(browser, options=options)
        self.driver.implicitly_wait(10)

    def download_img(self, url, filename):
        response = requests.get(url)
        bucket = "rpa-challenge-pictures"
        self.s3.put_object(Bucket=bucket, Key=filename, Body=response.content)
        self.logger.info(f"Image {filename} uploaded to s3")
        url = self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": filename}, ExpiresIn=360000
        )
        return url

    def parse_news_article(self, news, search_term):
        content = self.driver.find_element(by=By.CLASS_NAME, value="promo-content")
        title_container =  content.find_element(By.CLASS_NAME, "promo-title-container")
        title = title_container.find_element(By.TAG_NAME, "h3").text
        try:
            description = content.find_element(By.CLASS_NAME, "promo-description").text
        except NoSuchElementException:
            description = ""
        created_at = content.find_element(By.CLASS_NAME, "promo-timestamp").get_attribute("data-timestamp")
        created_at = datetime.fromtimestamp(int(created_at) / 1000)
        re_exp1 = r"\$?[0-9,.]+"
        re_exp2 = r"\d+[dollars|usd]"
        amount_of_money = re.findall(
            re_exp1, title + description, re.IGNORECASE
        ) or re.findall(re_exp2, title + description, re.IGNORECASE)
        amount_of_money = True if len(amount_of_money) > 0 else False
        count_search_phrase = len(
            re.findall(search_term, title + description, re.IGNORECASE)
        )
        try:
            media = news.find_element(By.CLASS_NAME, "promo-media")
            image = media.find_element(By.TAG_NAME, "img").get_attribute("src")
            url = media.find_element(By.TAG_NAME, "a").get_attribute("href")
            filename = title.replace(" ", "_")
            filename = re.sub(r"[^a-zA-Z0-9_]", "", filename).lower() + ".jpg"
            output_filename = filename
        except NoSuchElementException:
            image = ""
            output_filename = ""
        img_url = self.download_img(image, filename=output_filename)
        news_data = {
            "title": title,
            "url": url,
            "description": description,
            "date": created_at,
            "image": img_url,
            "amount_of_money": amount_of_money,
            "count_search_phrase": count_search_phrase,
        }
        return news_data

    def search_by_term(self):
        try:
            page = self.driver.find_element(by=By.CLASS_NAME, value="page-body")
            search_icon = page.find_element(
                by=By.XPATH, value="//button[@data-element='search-button']"
            )
            search_icon.click()
            search_bar = self.driver.find_element(
                by=By.XPATH, value="//div[@data-element='search-overlay']"
            )
            search_input = search_bar.find_element(
                by=By.XPATH, value="//input[@name='q']"
            )
            search_input.send_keys(self.search_term)
            search_button = search_bar.find_element(
                by=By.XPATH, value="//button[@type='submit']"
            )
            search_button.click()

        except NoSuchElementException:
            self.logger.warning("Search bar not found")
            self.logger.info("Trying to search using the URL")
            self.driver.get(self.url + f"/search?q={self.search_term}")

    def set_category(self):
        page = self.driver.find_element(by=By.CLASS_NAME, value="page-content")
        self.logger.info(f"Selecting category {self.category}")
        category_toggler = page.find_element(by=By.TAG_NAME, value="ps-toggler")
        see_all = category_toggler.find_element(
            by=By.XPATH, value="//span[contains(.,'See All')]"
        )
        see_all.click()
        try:
            category_element = category_toggler.find_element(
                by=By.XPATH, value=f"//span[contains(.,'{self.category}')]"
            )
            category_element.click()

        except NoSuchElementException:
            self.logger.warning(f"Category {self.category} not found")
        except Exception as e:
            self.logger.error(f"Error selecting category {self.category}: {e}")
            self.logger.info("Continuing without selecting category")

    def sort_by(self, value="1"):
        # I had to force the sorting by newest because there was a problem that I could not solve
        # a loading div appears but all the waits strategies I tried did not work
        # because this all the elements were already there
        # really dont know how to solve it
        self.logger.info("Sorting by newest")
        updated_url = self.driver.current_url
        updated_url = re.sub(r"&s=\d", f"&s={value}", updated_url)
        self.driver.get(updated_url)

    def click_next_button(self, page):
        next_button = page.find_element(
            by=By.CLASS_NAME, value="search-results-module-next-page"
        )

        try:
            next_button.click()
        except ElementClickInterceptedException:
            self.logger.error(f"next button not available")
            self.logger.info("ending scraping")
            return False
        return True


@task
def run_crawler():

    for item in workitems.inputs:
        url = item.payload.get("url")
        search_term = item.payload.get("search_term")
        num_months = item.payload.get("num_months")
        category = item.payload.get("category")
        crawler = Crawler(url, search_term, num_months, category)
        crawler.logger.info(f"Processing item: {item.payload}")
        crawler.set_webdriver()
        crawler.driver.get(url)
        crawler.search_by_term()
        if category:
            crawler.set_category()
        crawler.sort_by("1")
        page = crawler.driver.find_element(by=By.CLASS_NAME, value="page-content")
        current_date = datetime.now()
        file_path = f"news.xlsx"
        crawler.create_workbook(file_path)
        crawler.logger.info("Starting to scrape news")
        while crawler.target_date <= current_date:
            results = crawler.driver.find_element(
                by = By.CLASS_NAME, value = "search-results-module-results-menu"
            )
            news_list = results.find_elements(by=By.TAG_NAME, value="ps-promo")
            page_news_data = []
            for news in news_list:
                news_data = crawler.parse_news_article(news, search_term)
                if news_data["date"] <= current_date:
                    current_date = news_data["date"]
                if news_data["date"] >= crawler.target_date:
                    news_data["date"] = news_data["date"].strftime("%Y-%m-%d %H:%M:%S")
                    news_title = news_data["title"]
                    page_news_data.append(news_data)
                    crawler.logger.info(f"News {news_title} added to list")
                else:
                    crawler.logger.warning("News date is older than target date")
            crawler.excel.append_rows_to_worksheet(news_data, header=True)
            continue_loop = crawler.click_next_button(page)
            if not continue_loop:
                break
            page = crawler.driver.find_element(by = By.CLASS_NAME, value = "page-content")
        item.done()
