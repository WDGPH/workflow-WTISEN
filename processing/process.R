##############
# Parameters #
##############

library(optparse)

logger = function(..., sep = ""){
  cat("\n", format(Sys.time(), format = '%Y-%m-%d %H:%M:%S'), " ", ..., sep = sep)}

parser = OptionParser(
  option_list = list(
    
    make_option(
      opt_str = c("-i", "--input"),
      help = "Input file, in CSV format.",
      type = "character",
      default = ""),

    make_option(
      opt_str = c("-o", "--output"),
      help = "Output file, in CSV format.",
      type = "character",
      default = ""),
    
    make_option(
      opt_str = c("-v", "--verbose"),
      help = "Print additional diagnostic information.",
      action = "store_true",
      default = FALSE)
    )
  )

# Parse arguments
args = parse_args(parser)

# Verbose argument
if(args$verbose){
  logger("The following arguments have been passed to R:",
    commandArgs(trailingOnly = TRUE))
  }

###################
# Data processing #
###################

# Disable package masking warnings for production
options(conflicts.policy = list("warn" = F))

library(readr)
library(tidyr)
library(dplyr)
library(stringr)
library(lubridate)

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

if(args$input != ""){
  logger("Reading unprocessed CSV file input from: ", args$input)
} else {
  logger("No input file specified, ending script")
  stop("No input file specified")
}

# Extract CSV content
wtisen_data = read_csv(
  file = args$input,
  guess_max = 0,
  show_col_types = FALSE) |>
  rename_with(.fn = \(x) {x |>
      str_to_upper() |>
      str_remove_all("^SRC_|^SUB_|2$")}) |>
  select(
    BARCODE,
    DATE_COLLECTED,
    DATE_RECEIVED,
    DATE_RELEASED,
    DATE_REPORTED,
    LABORATORY,
    PHONE,
    ALT_PHONE,
    FIRST_NAME,
    LAST_NAME,
    ADDRESS,
    LOT_NUM,
    CONCESSION,
    CITY,
    MUNICIPALITY,
    COUNTY,
    EMERGENCY_LOC_NO,
    POSTAL,
    ENTRY,
    FORMATTED_ENTRY,
    TOTAL_COLIFORM,
    E_COLI,
    REQ_LEGIBLE) |>
  mutate(
    across(
      .cols = c(
        "ADDRESS",
        "CITY",
        "MUNICIPALITY",
        "COUNTY"),
      .fns = \(x) str_replace(x, "_", " ")),
    across(
      .cols = starts_with("DATE_"),
      .fns = \(x) {x |>
          as_datetime(format = c("%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S")) |>
          force_tz(tz = "America/Toronto")}),
    across(
      .cols = where(is.character),
      .fns  = \(x) str_trim(x)),
    POSTAL = postalcode_cleaner(POSTAL),
    ENTRY = as.integer(ENTRY),
    REQ_LEGIBLE = str_detect(REQ_LEGIBLE, "^y|Y$"))

logger("Data loaded and processed")
logger("Dimensions: ", dim(wtisen_data)[1], " x ", dim(wtisen_data)[2])

if(args$output != ""){
  arrow::write_parquet(wtisen_data, args$output)
  logger("Processed data output in parquet format to: ", args$output)
} else {
  logger("No output location specified, skipping data output") 
}

logger("Done!")