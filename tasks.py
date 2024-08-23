import os
from pathlib import Path
import requests
from robocorp import browser, workitems
from robocorp.tasks import task
from RPA.Browser.Selenium import Selenium, ElementNotFound
from RPA.Excel.Files import Files as Excel
from datetime import datetime
import requests
import re
import logging
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("ROBOT_ARTIFACTS", "output"))

@task
def producer():
    def init_browser():
        browser.configure(
            browser_engine="chromium", 
            screenshot="only-on-failure", 
            headless=True 
        )
        selenium = Selenium(
            timeout = 60,
            implicit_wait = 2,
            run_on_failure = "Capture Page Screenshot",
        )

        return selenium

    def download_img(url, filename):
        response = requests.get(url)
        with open(filename, "wb") as file:
            file.write(response.content)
        return filename

    def _get_target_date(num_months):
        target_date = datetime.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        if num_months == 2:
            target_date = (datetime.now() - relativedelta(months=1)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        elif num_months == 3:
            target_date = (datetime.now() - relativedelta(months=2)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        logger.info(f"Target date: {target_date}")
        return target_date        

    def parse_news(news, search_term, selenium):
        content = selenium.find_element("class:promo-content", news)
        title_container = selenium.find_element("class:promo-title-container", content)
        title = selenium.find_element("tag:h3", title_container).text
        url = selenium.find_element("tag:a", title_container).get_attribute("href")
        try:
            description = selenium.find_element("class:promo-description", content).text
        except ElementNotFound:
            description = ""
        created_at = selenium.find_element("class:promo-timestamp", content).get_attribute("data-timestamp")
        created_at = datetime.fromtimestamp(int(created_at) / 1000)
        re_exp1 = r"\$?[0-9,.]+"
        re_exp2 = r"\d+[dollars|usd]"
        amount_of_money = (re.findall(re_exp1, title+description, re.IGNORECASE) or 
                                    re.findall(re_exp2, title+description, re.IGNORECASE))
        amount_of_money = True if len(amount_of_money) > 0 else False
        count_search_phrase = len(re.findall(search_term, title+description, re.IGNORECASE))
        try:
            media = selenium.find_element("class:promo-media", news)
            image = selenium.find_element("tag:img", media).get_attribute("src")
            filename = title.replace(" ", "_")
            filename = re.sub(r"[^a-zA-Z0-9_]", "", filename).lower() + ".jpg"
            output_filename = OUTPUT_DIR / filename
        except ElementNotFound:
            image = ""
            output_filename = ""
        download_img(image, filename=output_filename)
        news_data = {
            "title": title,
            "url": url,
            "description": description,
            "date": created_at,
            "image": str(output_filename),
            "amount_of_money": amount_of_money,
            "count_search_phrase": count_search_phrase
        }
        return news_data
    
    def run_task():
        for item in workitems.inputs:
            logger.info(f"Processing item: {item}")
            
            url = item.payload.get("url")
            search_term = item.payload.get("search_term")
            num_months = item.payload.get("num_months")
            category = item.payload.get("category")
            
            target_date = _get_target_date(num_months)
            logger.info(f"Target date: {target_date}")
            
            selenium = init_browser()
            logger.info(f"Opening URL: {url}")
            selenium.open_available_browser(url)

            logger.info(f"inputing search term {search_term}")
            try:
                selenium.wait_until_page_contains_element("class:page-body", timeout=15)
                page = selenium.find_element("class:page-body")
                search_button = selenium.find_element("xpath://button[@data-element='search-button']", page)
                selenium.click_element(search_button)
                search_bar = selenium.wait_until_page_contains_element("xpath://div[@data-element='search-overlay']", timeout=15)
                search_input = selenium.find_element("xpath://input[@name='q']", search_bar)
                selenium.input_text(search_input, text=search_term)
                search_button = selenium.find_element("xpath://button[@type='submit']", search_bar)
                selenium.click_element(search_button)
                
            except ElementNotFound:
                logger.warning("Search bar not found")
                logger.info("Trying to search using the URL")
                selenium.go_to(url + f"/search?q={search_term}")
            finally:
                selenium.wait_until_page_contains_element("class:promo-content", timeout=15)
            
            if category:
                page = selenium.find_element("class:page-content")
                logger.info(f"Selecting category {category}")
                category_toggler = selenium.find_element("xpath://ps-toggler", page)
                see_all = selenium.find_element("xpath://span[contains(.,'See All')]", category_toggler)
                selenium.click_element(see_all)
                try:
                    category_element = selenium.find_element(f"xpath=//span[contains(.,'{category}')]", category_toggler)
                    selenium.click_element(category_element)
                    selenium.wait_until_element_is_not_visible("class:loading-icon", timeout=15)
                except ElementNotFound:
                    logger.warning(f"Category {category} not found")
                except Exception as e:
                    logger.error(f"Error selecting category {category}: {e}")
                    logger.info("Continuing without selecting category")
                    continue
            
            # I had to force the sorting by newest because there was a problem that I could not solve
            # a loading div appears but all the waits strategies I tried did not work
            # because this all the elements were already there
            # really dont know how to solve it
            logger.info("Sorting by newest")
            updated_url = selenium.get_location()
            selenium.go_to(updated_url.replace("&s=0", "&s=1"))

            current_date = datetime.now()
            logger.info("Starting to scrape news")

            while target_date <= current_date:
                    page = selenium.find_element("class:page-content")
                    results = selenium.find_element("class:search-results-module-results-menu", page)
                    news_list = selenium.find_elements("tag:ps-promo", results)
                    for news in news_list:
                        print(news.text)
                        news_data = parse_news(news=news, search_term=search_term, selenium=selenium)

                        if news_data["date"] <= current_date:
                            current_date = news_data["date"]
                            
                        if news_data["date"] >= target_date:
                            news_data["date"] = news_data["date"].strftime("%Y-%m-%d %H:%M:%S")
                            workitems.outputs.create(news_data)
                            news_title = news_data["title"]
                            logger.info(f"News {news_title} added to output")
                        else:
                            logger.warning("News date is older than target date")
                            
                    
                    page_num = selenium.find_element("class:search-results-module-page-counts", page).text
                    page_num = page_num.split(" ")[0]
                    logger.info(f"Page {page_num} done")
                    next_button = selenium.find_element("class:search-results-module-next-page", page)
                    try:
                        selenium.click_element(next_button)
                    except Exception as e:
                        if selenium.is_element_enabled("class:search-results-module-next-page"):
                            logger.error(f"next button not available: {e}")
                            logger.info("ending scraping")
                            break
            
            selenium.close_browser()
            item.done()

    run_task()
                
@task
def consumer():
    excel = Excel()
    file_path = OUTPUT_DIR / "news.xlsx"
    excel.create_workbook(file_path)

    for item in workitems.inputs:
        news_data = item.payload
        excel.append_rows_to_worksheet(news_data, header=True)

        item.done()
    excel.save_workbook(file_path)
