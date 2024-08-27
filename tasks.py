import os
from pathlib import Path
import requests
from robocorp import browser, workitems, vault
from robocorp.tasks import task
from RPA.Browser.Selenium import Selenium, ElementNotFound
from RPA.Excel.Files import Files as Excel
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


logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("ROBOT_ARTIFACTS", "output"))


class Crawler:
    def __init__(self, url, search_term, num_months, category):
        self.url = url
        self.search_term = search_term
        self.num_months = num_months
        self.category = category
        self.target_date = self._get_target_date()
        self.s3 = client("s3")

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
        logger.info(f"Target date: {target_date}")
        return target_date

    def init_browser(self):
        browser.configure(
            browser_engine="chromium", screenshot="only-on-failure", headless=True
        )
        self.selenium = Selenium(timeout=60, implicit_wait=2)
        self.selenium.open_available_browser(self.url)

    def download_img(self, url, filename):
        response = requests.get(url)
        bucket = "rpa-challenge-pictures"
        self.s3.put_object(Bucket=bucket, Key=filename, Body=response.content)
        logger.info(f"Image {filename} uploaded to s3")
        url = self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": filename}, ExpiresIn=360000
        )
        return url

    def parse_news_article(self, news, search_term):
        content = self.selenium.find_element("class:promo-content", news)
        title_container = self.selenium.find_element(
            "class:promo-title-container", content
        )
        title = self.selenium.find_element("tag:h3", title_container).text
        try:
            description = self.selenium.find_element(
                "class:promo-description", content
            ).text
        except ElementNotFound:
            description = ""
        created_at = self.selenium.find_element(
            "class:promo-timestamp", content
        ).get_attribute("data-timestamp")
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
            media = self.selenium.find_element("class:promo-media", news)
            image = self.selenium.find_element("tag:img", media).get_attribute("src")
            url = self.selenium.find_element("tag:a", media).get_attribute("href")
            filename = title.replace(" ", "_")
            filename = re.sub(r"[^a-zA-Z0-9_]", "", filename).lower() + ".jpg"
            output_filename = filename
        except ElementNotFound:
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
            self.selenium.wait_until_page_contains_element(
                "class:page-body", timeout=15
            )
            page = self.selenium.find_element("class:page-body")
            search_button = self.selenium.find_element(
                "xpath://button[@data-element='search-button']", page
            )
            self.selenium.click_element(search_button)
            search_bar = self.selenium.wait_until_page_contains_element(
                "xpath://div[@data-element='search-overlay']", timeout=15
            )
            search_input = self.selenium.find_element(
                "xpath://input[@name='q']", search_bar
            )
            self.selenium.input_text(search_input, text=self.search_term)
            search_button = self.selenium.find_element(
                "xpath://button[@type='submit']", search_bar
            )
            self.selenium.click_element(search_button)

        except ElementNotFound:
            logger.warning("Search bar not found")
            logger.info("Trying to search using the URL")
            self.selenium.go_to(self.url + f"/search?q={self.search_term}")
        finally:
            self.selenium.wait_until_page_contains_element(
                "class:page-content", timeout=15
            )

    def set_category(self):
        page = self.selenium.find_element("class:page-content")
        logger.info(f"Selecting category {self.category}")
        category_toggler = self.selenium.find_element("xpath://ps-toggler", page)
        see_all = self.selenium.find_element(
            "xpath://span[contains(.,'See All')]", category_toggler
        )
        self.selenium.click_element(see_all)
        try:
            category_element = self.selenium.find_element(
                f"xpath=//span[contains(.,'{self.category}')]", category_toggler
            )
            self.selenium.click_element(category_element)
            self.selenium.wait_until_element_is_not_visible(
                "class:loading-icon", timeout=15
            )
        except ElementNotFound:
            logger.warning(f"Category {self.category} not found")
        except Exception as e:
            logger.error(f"Error selecting category {self.category}: {e}")
            logger.info("Continuing without selecting category")

    def sort_by(self, value="1"):
        # I had to force the sorting by newest because there was a problem that I could not solve
        # a loading div appears but all the waits strategies I tried did not work
        # because this all the elements were already there
        # really dont know how to solve it
        logger.info("Sorting by newest")
        updated_url = self.selenium.get_location()
        updated_url = re.sub(r"&s=\d", f"&s={value}", updated_url)
        self.selenium.go_to(updated_url)

    def click_next_button(self, page):
        next_button = self.selenium.find_element(
            "class:search-results-module-next-page", page
        )
        try:
            self.selenium.click_element(next_button)
        except Exception as e:
            if self.selenium.is_element_enabled(
                "class:search-results-module-next-page"
            ):
                logger.error(f"next button not available: {e}")
                logger.info("ending scraping")
            return False
        return True


@task
def run_crawler():
    for item in workitems.inputs:
        logger.info(f"Processing item: {item.payload}")
        url = item.payload.get("url")
        search_term = item.payload.get("search_term")
        num_months = item.payload.get("num_months")
        category = item.payload.get("category")
        crawler = Crawler(url, search_term, num_months, category)
        crawler.init_browser()
        crawler.search_by_term()
        if category:
            crawler.set_category()
        crawler.sort_by("1")
        page = crawler.selenium.find_element("class:page-content")
        current_date = datetime.now()
        file_path = f"news.xlsx"
        crawler.create_workbook(file_path)
        logger.info("Starting to scrape news")
        while crawler.target_date <= current_date:
            results = crawler.selenium.find_element(
                "class:search-results-module-results-menu", page
            )
            news_list = crawler.selenium.find_elements("tag:ps-promo", results)
            page_news_data = []
            for news in news_list:
                news_data = crawler.parse_news_article(news, search_term)
                if news_data["date"] <= current_date:
                    current_date = news_data["date"]
                if news_data["date"] >= crawler.target_date:
                    news_data["date"] = news_data["date"].strftime("%Y-%m-%d %H:%M:%S")
                    news_title = news_data["title"]
                    page_news_data.append(news_data)
                    logger.info(f"News {news_title} added to list")
                else:
                    logger.warning("News date is older than target date")
            crawler.excel.append_rows_to_worksheet(news_data, header=True)
            continue_loop = crawler.click_next_button(page)
            if not continue_loop:
                break
            page = crawler.selenium.find_element("class:page-content")
        crawler.selenium.close_browser()
        item.done()
