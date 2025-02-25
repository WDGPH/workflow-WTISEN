import logging
import argparse
from time import sleep
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
import requests
import pandas as pd
import io

# Argument parser
def parse_args():
  parser = argparse.ArgumentParser(
    description='Fetches well water testing results from Public Health Ontario')

  parser.add_argument(
    '--url',
    help = 'URL to access Public Health Ontario well water testing result portal',
    required = True,
    type = str)

  parser.add_argument(
    '--report',
    help = 'Report name, preceded in report URL by `/RSReports/` and ends with `.rdl`',
    required = True,
    type = str)

  parser.add_argument(
    '--phu',
    help = "Public Health Unit ID (4 numeric characters)",
    required = True,
    type = str)

  parser.add_argument(
    '--start',
    help = "Start date for retrieved records (YYYY-MM-DD format)",
    required = True,
    type = str)

  parser.add_argument(
    '--end',
    help = "End date for retrieved records (YYYY-MM-DD format)",
    required = True,
    type = str)
  
  parser.add_argument(
    '--output',
    help = "Filename to write output to",
    required = True,
    type = str)
  
  # New arguments for login credentials
  parser.add_argument(
    '--username',
    help = "Path to file containing username for PHO portal",
    required = True,
    type = str)
    
  parser.add_argument(
    '--password',
    help = "Path to file containing password for PHO portal",
    required = True,
    type = str)
  
  return parser.parse_args()


# Main function to extract and output data from PHO WTISEN
def main(url, report, phu, start, end, output, username_file, password_file):
  # Read credentials from files
  with open(username_file, 'r') as file:
    username = file.read().strip()
  
  with open(password_file, 'r') as file:
    password = file.read().strip()
    
  # Check if phu is in expected format
  if not phu.isnumeric() or len(phu) != 4:
    raise ValueError("PHU must be exactly 4 numeric characters.")
  else:
    logging.info(f"Data requested for PHU {phu}")
  
  # Parse start and end dates strings into times
  start = datetime.strptime(start, "%Y-%m-%d").date()
  end = datetime.strptime(end, "%Y-%m-%d").date()
  if start <= end:
    logging.info(f"Data requested from {start} to {end}")
  else:    
    raise ValueError(f"Invalid date range provided ({start} to {end})")
  
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
  
  # Quit the browser
  browser.quit()
  logging.info("Browser has quit")
  
  # Create intervals of max length 3 years for data download
  date_intervals = []
  current_start = start
  while current_start <= end:
    current_end = current_start + timedelta(days = 3*365)
    if current_end > end:
      current_end = end
    date_intervals.append((current_start, current_end))
    current_start = current_end + timedelta(days = 1)
  
  logging.info(f"Data will be downloaded in {len(date_intervals)} batches")

  # Initialize df as None to support loop
  df = None

  for date_interval in date_intervals:
    logging.info(f"Retrieving data released between {date_interval[0]} and {date_interval[1]}")
  
    ## Create a download URL using specified parameters
    dl_url = ''.join([
      url, '/_vti_bin/ReportServer?', url,
      
      #### Report name
      '/RSReports/', report,
      
      #### Item of interest within report
      '&rc:ItemPath=Tablix4',
      
      #### Report parameters - PHU Number
      '&prmPHU=', phu,
      
      #### Report parameters - Start and End Dates
      '&prmSelDate=0',
      '&prmStartdate=', date_interval[0].strftime('%#m/%d/%Y 00:00:00'),
      '&prmEnddate=', date_interval[1].strftime('%#m/%d/%Y 23:59:59'),
      
      #### Report parameters - Duplicate and null value behaviour
      '&prmDuplicates=0',
      '&prmrender:isnull=True',
      
      #### Reporting Service parameters - Formatting information
      '&rs:ParameterLanguage=',
      '&rs:Command=Render',
      '&rs:Format=CSV'
    ])
    
    logging.info(f"Report download URL prepared: {dl_url}")

    ## Wait 10 seconds between downloads
    sleep(10)

    ## Use requests session to retrieve data at URL
    response = session.get(dl_url)
    
    if response.status_code == 200:
      logging.info(f"Response received with Status Code {response.status_code} in {response.elapsed.total_seconds():.2f} seconds")
    else:
      raise RuntimeError(f"Response received with Status Code {response.status_code}")

    temp_df = pd.read_csv(io.StringIO(response.content.decode('utf-8')), skiprows = 3, dtype = str)
    
    if df is None:
      df = temp_df
    else:
      df = pd.concat([df, temp_df], ignore_index = True)
  
  # Write the combined dataframe to a single CSV file
  if df is not None:
    df.to_csv(output, index = False)
    logging.info(f"All data written to {output}")
  else:
    logging.warning("No data to write.")


if __name__ == '__main__':
  logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)
  
  # Parse and unpack keyword arguments
  main(**vars(parse_args()))
  logging.info("Done")