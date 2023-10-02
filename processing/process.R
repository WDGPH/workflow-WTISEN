cat(
  "WTISEN Result Pre-processing", 
  "############################",
  sep = "\n")

cat("\nWorking directory:", getwd(),"\n")

# Disable package masking warnings for production
options(conflicts.policy = list('warn' = F))

# Load libraries
library(readr)
library(tidyr)
library(dplyr)
library(stringr)
library(lubridate)

# Script arguments
cat(
  "\n\nThis script requires 2 arguments:",
  "The path of the CSV export from PHO WTISEN",
  "The path of the processed parquet output", sep = "\n-"
)

args = commandArgs(trailingOnly = T)

cat("\nArguments detected:", args, sep = "\n-")

wtisen_input = args[1]
wtisen_output = args[2]

# Extract top content of CSV for logging
read_csv(
  file           = wtisen_input,
  skip           = 1,
  n_max          = 1,
  col_names      = F,
  col_select     = 2,
  show_col_types = F) %>%
  pull(1) %>%
  str_replace_all(c(
    "--"    = "\n",
    " {2,}" = " ",
    "\r"    = "",
    "\n "   = "\n")) %>%
  cat("\nFile info from PHO WTISEN:", ., sep = "\n")

# Utility function
# Postal code cleaner
postalcode_check = function(x){
  nx = nchar(x)
  case_when(
    nx == 6 ~ str_detect(x, "^[ABCEGHJ-NP-TVXY][0-9][ABCEGHJ-NP-TV-Z][0-9][ABCEGHJ-NP-TV-Z][0-9]"),
    nx == 5 ~ str_detect(x, "^[ABCEGHJ-NP-TVXY][0-9][ABCEGHJ-NP-TV-Z][0-9][ABCEGHJ-NP-TV-Z]"),
    nx == 4 ~ str_detect(x, "^[ABCEGHJ-NP-TVXY][0-9][ABCEGHJ-NP-TV-Z][0-9]"),
    nx == 3 ~ str_detect(x, "^[ABCEGHJ-NP-TVXY][0-9][ABCEGHJ-NP-TV-Z]"))}

postalcode_cleaner = function(x){
  x = str_remove_all(x, "[\\W_]")
  x = toupper(x)
  # Take first and last 3 characters if longer than 6
  x = case_when(nchar(x) > 6 ~ paste0(str_extract(x, "^[A-Z0-9]{3,3}"), str_extract(x, "[A-Z0-9]{3,3}$")),
                TRUE ~ x)
  # Ohs and Zeros
  # Eyes/Els and Ones
  x = str_replace_all(x, c("^([ABCEGHJ-NP-TVXY])O([ABCEGHJ-NP-TV-Z])"    = "\\10\\2",
                           "([ABCEGHJ-NP-TV-Z])O([ABCEGHJ-NP-TV-Z])"     = "\\10\\2",
                           "([ABCEGHJ-NP-TV-Z])O$"                       = "\\10",
                           "^([ABCEGHJ-NP-TVXY])[IL]([ABCEGHJ-NP-TV-Z])" = "\\11\\2",
                           "([ABCEGHJ-NP-TV-Z])[IL]([ABCEGHJ-NP-TV-Z])"  = "\\11\\2",
                           "([ABCEGHJ-NP-TV-Z])[IL]$"                    = "\\11"))

  # Possible 6 to 5 characters
  # Chop off a character each time it fails
  check = postalcode_check(x)
  x = case_when(
    is.na(check) ~ NA_character_,
    !check ~ str_sub(x, 1L, -2L),
    check ~ x
  )
  
  # Possible 5 to 4 characters
  check = postalcode_check(x)
  x = case_when(
    is.na(check) ~ NA_character_,
    !check ~ str_sub(x, 1L, -2L),
    check ~ x
  )
  
  # Finally a possible 4 to 3 characters
  check = postalcode_check(x)
  x = case_when(
    is.na(check) ~ NA_character_,
    !check ~ str_sub(x, 1L, -2L),
    check ~ x
  )

  # Final check
  check = postalcode_check(x)
  x = case_when(
    is.na(check) ~ NA_character_,
    check ~ x,
    TRUE ~ NA_character_
  )
  
  return(x)
}

date_bounds = interval(as.POSIXct('2008-01-01'), Sys.Date())

# Extract CSV content
wtisen_data = read_csv(
  file = wtisen_input,
  skip = 3,
  col_types = cols_only(
    DATE_Collected       = col_datetime(format = "%m/%d/%Y %I:%M:%S %p"),
    DATE_RECEIVED        = col_datetime(format = "%m/%d/%Y %I:%M:%S %p"),
    Barcode              = col_character(),
    Laboratory           = col_character(),
    Sub_Phone            = col_character(),
    Sub_Alt_Phone        = col_character(),
    Sub_First_Name2      = col_character(),
    Sub_Last_Name2       = col_character(),
    SRC_ADDRESS          = col_character(),
    SRC_LOT_NUM          = col_character(),
    SRC_CONCESSION       = col_character(),
    SRC_CITY             = col_character(),
    SRC_MUNICIPALITY     = col_character(),
    SRC_COUNTY           = col_character(),
    SRC_EMERGENCY_LOC_NO = col_character(),
    SRC_POSTAL           = col_character(),
    ENTRY                = col_integer(),
    FORMATTED_ENTRY      = col_character(),
    TOTAL_COLIFORM       = col_character(),
    E_COLI               = col_character(),
    DATE_RELEASED        = col_datetime(format = "%Y-%m-%d %H:%M:%S"),
    DATE_REPORTED        = col_datetime(format = "%Y-%m-%d %H:%M:%S"),
    REQ_LEGIBLE          = col_character())) %>%
  rename_with(.fn = ~str_remove_all(str_to_upper(.x), "^SRC_|^SUB_|2$")) %>%
  mutate(
    across(
      .cols = c(
        "ADDRESS",
        "CITY",
        "MUNICIPALITY",
        "COUNTY"),
      .fns = ~str_replace(.x, "_", " ")),
    across(
      .cols = where(is.character),
      .fns  = str_trim),
    across(
      .cols = starts_with("DATE"),
      .fns  = ~force_tz(.x, tz = "America/Toronto")),
    across(
      .cols = starts_with("DATE"),
      .fns  = ~if_else(.x %within% date_bounds, .x, NA_POSIXct_)),
    POSTAL = postalcode_cleaner(POSTAL),
    REQ_LEGIBLE = str_detect(REQ_LEGIBLE, "^y|Y$")) %>%
  relocate("BARCODE", "REQ_LEGIBLE", starts_with("DATE"))

cat("\nData loaded and processed")
cat("\nDimensions: ", dim(wtisen_data)[1], " x ", dim(wtisen_data)[2], "\n", sep = "")
cat("\nFields:", names(wtisen_data), sep = "\n-")

arrow::write_parquet(wtisen_data, wtisen_output)
cat("\nPre-processed data output to: ", wtisen_output, sep = "")

cat("\n\nDone!")