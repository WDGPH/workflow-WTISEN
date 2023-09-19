import os
import logging
import argparse
from time import sleep
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
import requests


# Argument parser
def parse_args():
  parser = argparse.ArgumentParser(
    description='Fetches well water testing results from Public Health Ontario for up to 3 years at a time')

  parser.add_argument(
    'url',
    help = "URL to access Public Health Ontario well water testing result portal",
    type = str)

  parser.add_argument(
    'phu',
    help = "Public Health Unit ID (4 numeric characters)",
    type = str)

  parser.add_argument(
    'start',
    help = "Start date for retrieved records (YYYY-MM-DD format)",
    type = str)

  parser.add_argument(
    'end',
    help = "End date for retrieved records (YYYY-MM-DD format)",
    type = str)
  
  parser.add_argument(
    'output',
    help = "Filename to write output to",
    type = str)
  
  return parser.parse_args()


# Main function to extract and output data from PHO WTISEN
def main(url, phu, start, end, output):
  
  # Check if phu is in expected format
  if not phu.isnumeric() or len(phu) != 4:
    raise ValueError("PHU must be exactly 4 numeric characters.")
  else:
    logging.info(f"Data requested for PHU {phu}")
  
  # Parse start and end dates strings into times
  start = datetime.strptime(start, "%Y-%m-%d").date()
  end = datetime.strptime(end, "%Y-%m-%d").date()
  logging.info(f"Data requested from {start} to {end}")
  
  # Check that start and end dates are less than 3 years apart
  if (end - start).days >= 365*3:
    raise ValueError("Date range exceeds 3 years, the maximum which can be retrieved from WTISEN in a single report")
  
  # Load credentials and remove environment variables
  username = os.getenv('WTISEN_USER')
  if username is not None:
    logging.info("WTISEN_USER environment variable found")
    os.environ.pop('WTISEN_USER', None)
  else:
    raise ValueError("WTISEN_USER environment variable not found.")
    
  password = os.getenv('WTISEN_PASSWORD')
  if password is not None:
    logging.info("WTISEN_PASSWORD environment variable found")
    os.environ.pop('WTISEN_PASSWORD', None)
  else:
    raise ValueError("WTISEN_PASSWORD environment variable not found.")

  # Start browser
  browser = webdriver.Firefox()
  logging.info("Browser started")

  # Wait up to 30 seconds for elements to appear
  browser.implicitly_wait(30)
  logging.info("Browser will wait up to 30 seconds for elements to appear")
  
  # Refresh pages up to 2 times
  max_attempts_per_step = 2
  logging.info("Browser will refresh each page up to 2 times if initially unavailable")
  
  # Go to PHO WTISEN login page
  logging.info(f"Getting {url}")
  browser.get(url)
  logging.info(f"Currently on page: {browser.current_url}")

  # Submit Email
  step1_attempts = 0
  step1_success = False

  while not step1_success and step1_attempts < max_attempts_per_step:
    try:
      sleep(10)
      browser.find_element(By.ID, "emailInput").send_keys(username)
      browser.find_element(By.CLASS_NAME, "submit").click()
      step1_success = True
    except Exception as e:
      logging.info(f"Could not complete step 1 on attempt {step1_attempts + 1}")
      logging.error(f"An exception occurred: {e}", exc_info = True)
      step1_attempts += 1
      browser.refresh()

  if step1_success:
    logging.info(f"Login email submitted ({username})")
  else:
    raise RuntimeError("Could not complete step with Selenium within allowed refreshes/wait period")

  ## Remove python variable
  del username

  # Submit Password
  step2_attempts = 0
  step2_success = False

  while not step2_success and step2_attempts < max_attempts_per_step:
    try:
      sleep(10)
      browser.find_element(By.ID, "passwordInput").send_keys(password)
      browser.find_element(By.CLASS_NAME, "submit").click()
      step2_success = True
    except Exception as e:
      logging.info(f"Could not complete step 2 on attempt {step2_attempts + 1}")
      logging.error(f"An exception occurred: {e}", exc_info = True)
      step2_attempts += 1
      browser.refresh()

  if step2_success:
    logging.info("Login password submitted")
  else:
    raise RuntimeError("Could not complete step with Selenium within allowed refreshes/wait period")

  ## Remove python variable
  del password

  # Check for login success
  try:
      error_message = browser.find_element(By.ID, "errorText").text
      raise RuntimeError(f"Login failed with error: {error_message}")
  except NoSuchElementException:
      # No error message found, proceed
      pass

  # Download data
  ## Transfer cookies from Selenium session to new requests session
  sleep(10)
  cookies = browser.get_cookies()

  if len(cookies) > 0:
    logging.info(f"{len(cookies)} Cookies found in Selenium Session")
  else:
    raise RuntimeError("No cookies retrieved from Selenium Session")

  session = requests.Session()
  logging.info("New Requests Session created")
  for cookie in cookies:
    logging.info(f"Adding Cookie {cookie['name']} to Requests Session...")
    session.cookies.set(cookie['name'], cookie['value'])
    
  logging.info("Cookies copied from Selenium to Requests")

  ## Create a download URL using specified parameters
  dl = url +\
    '/_vti_bin/ReportServer?' +\
    url +\
    '/RSReports/Private+Water+Testing+Information+Summary+Report+-+WTISEN+-+PHU+Report.rdl&prmPHU=' +\
    phu +\
    '&prmSelDate=0&prmStartdate=' +\
    start.strftime('%#m/%d/%Y') +\
    '%2000%3A00%3A00&prmEnddate=' +\
    end.strftime('%#m/%d/%Y') +\
    '%2000%3A00%3A00&prmrender%3Aisnull=True&prmDuplicates=0&rs%3AParameterLanguage=&rs%3ACommand=Render&rs%3AFormat=CSV&rc%3AItemPath=Tablix4'
  
  logging.info("Report download URL prepared")

  ## Use requests session to retrieve data at URL
  response = session.get(dl)
  if response.status_code == 200:
    logging.info(f"Response received with Status Code {response.status_code} in {response.elapsed.total_seconds():.2f} seconds")
  else:
    raise RuntimeError(f"Response received with Status Code {response.status_code}")

  ## Write the response to the output file
  with open(output, 'wb') as f:
    f.write(response.content)
  logging.info(f"Response written to {output}")

  # Step 5: Quit the browser
  browser.quit()
  logging.info("Browser has quit")


if __name__ == '__main__':
  logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)
  # Parse and unpack keyword arguments
  main(**vars(parse_args()))
  logging.info("Done")