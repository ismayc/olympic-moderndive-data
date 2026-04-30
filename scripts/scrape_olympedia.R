#!/usr/bin/env Rscript
# scrape_olympedia.R
# ==================
# R port of scrape_olympedia.py: scrapes olympedia.org for the editions added
# since the rgriff23/Olympic_history dataset's 2016 cutoff. Produces a CSV in
# the same 15-column schema as the original athlete_events.csv:
#
#     ID, Name, Sex, Age, Height, Weight, Team, NOC, Games, Year, Season,
#     City, Sport, Event, Medal
#
# Olympedia edition IDs (verified by visiting each /editions/<id> page):
#     2018 Winter   60   PyeongChang
#     2020 Summer   61   Tokyo (held 2021)
#     2022 Winter   62   Beijing
#     2024 Summer   63   Paris
#     2026 Winter   72   Milano-Cortina
#
# USAGE
# -----
#     Rscript scrape_olympedia.R --editions 60,61,62,63,72 --out new_athletes.csv
#
# DEPENDENCIES
# ------------
#     install.packages(c("httr", "rvest", "dplyr", "tibble", "stringr",
#                        "purrr", "optparse"))
#
# This script mirrors the Python version's behavior (1.0s polite delay,
# per-edition checkpoints, bio cache across editions) so the two outputs
# can be diff'd row-for-row to spot scraper bugs.

suppressPackageStartupMessages({
  library(httr)
  library(rvest)
  library(dplyr)
  library(tibble)
  library(stringr)
  library(purrr)
  library(optparse)
})

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

BASE          <- "https://www.olympedia.org"
DEFAULT_DELAY <- 1.0   # seconds between HTTP requests; do not lower lightly
USER_AGENT    <- paste(
  "OlympicHistoryUpdater/1.0",
  "(github.com/<your-fork>/Olympic_history; contact: you@example.com)"
)

COLUMNS <- c("ID", "Name", "Sex", "Age", "Height", "Weight", "Team", "NOC",
             "Games", "Year", "Season", "City", "Sport", "Event", "Medal")

# Coalescing helper: returns the first arg that is non-NULL, non-NA, non-empty.
`%||%` <- function(a, b) {
  if (is.null(a) || length(a) == 0) return(b)
  if (is.character(a) && all(is.na(a) | a == "")) return(b)
  if (length(a) == 1 && is.na(a)) return(b)
  a
}

# ----------------------------------------------------------------------------
# HTTP layer (closure-based "fetcher" with rate limiting)
# ----------------------------------------------------------------------------

make_fetcher <- function(delay = DEFAULT_DELAY) {
  state <- new.env(parent = emptyenv())
  state$last <- 0
  list(
    get = function(path) {
      url  <- paste0(BASE, path)
      wait <- delay - (as.numeric(Sys.time()) - state$last)
      if (wait > 0) Sys.sleep(wait)
      resp <- GET(url, user_agent(USER_AGENT), timeout(30))
      state$last <- as.numeric(Sys.time())
      stop_for_status(resp)
      read_html(resp)
    }
  )
}

# ----------------------------------------------------------------------------
# Edition page parsing
# ----------------------------------------------------------------------------

parse_edition <- function(soup, edition_id) {
  h1 <- soup %>% html_element("h1") %>% html_text2()
  m  <- str_match(h1, "(\\d{4})\\s+(Summer|Winter)\\s+Olympics")
  if (is.na(m[1, 1])) {
    stop(sprintf("Unexpected edition title: %s", h1))
  }
  year   <- as.integer(m[1, 2])
  season <- m[1, 3]

  # City is in the Facts table, in the row with "Host city" in column 1
  city <- ""
  for (tr in soup %>% html_elements("table tr")) {
    cells <- tr %>% html_elements("td")
    if (length(cells) == 2 &&
        str_detect(html_text2(cells[[1]]), "Host city")) {
      city <- html_text2(cells[[2]]) %>%
        str_replace(",.*$", "") %>%
        str_trim()
      break
    }
  }

  list(edition_id  = edition_id,
       year        = year,
       season      = season,
       city        = city,
       games_label = paste(year, season))
}

list_sports <- function(soup) {
  links <- soup %>% html_elements('a[href*="/sports/"]')
  hrefs <- html_attr(links, "href")
  texts <- html_text2(links)

  keep  <- str_detect(hrefs, "/editions/\\d+/sports/[A-Z]{3}$")
  hrefs <- hrefs[keep]
  texts <- texts[keep]

  uniq <- !duplicated(hrefs)
  tibble(name = texts[uniq], href = hrefs[uniq])
}

# ----------------------------------------------------------------------------
# Sport / event / results parsing
# ----------------------------------------------------------------------------

list_events_for_sport <- function(soup) {
  links <- soup %>% html_elements('a[href*="/results/"]')
  hrefs <- html_attr(links, "href")
  texts <- html_text2(links)

  keep  <- str_detect(hrefs, "/results/\\d+")
  hrefs <- hrefs[keep]
  texts <- texts[keep]

  uniq <- !duplicated(hrefs)
  tibble(name = texts[uniq], href = hrefs[uniq])
}

parse_results_table <- function(soup) {
  out <- list()

  for (table in soup %>% html_elements("table.table")) {
    headers <- table %>% html_elements("thead th") %>%
      html_text2() %>% tolower()
    if (length(headers) == 0 || !any(str_detect(headers, "athlete"))) next

    for (tr in table %>% html_elements("tbody tr")) {
      cells <- tr %>% html_elements("td, th")
      if (length(cells) == 0) next

      values <- html_text2(cells)
      n      <- min(length(values), length(headers))
      row    <- as.list(values[seq_len(n)])
      names(row) <- headers[seq_len(n)]

      ath <- tr %>% html_element('a[href^="/athletes/"]')
      if (!is.na(ath)) {
        row$athlete_path <- html_attr(ath, "href")
        row$athlete      <- html_text2(ath)
      } else {
        row$athlete_path <- NA_character_
        row$athlete      <- row$athlete %||% ""
      }

      # Detect medal from the row's CSS class (olympedia uses gold/silver/bronze)
      cls <- (html_attr(tr, "class") %||% "") %>% tolower()
      row$medal <- if (str_detect(cls, "gold"))   "Gold"
                   else if (str_detect(cls, "silver")) "Silver"
                   else if (str_detect(cls, "bronze")) "Bronze"
                   else NA_character_

      out[[length(out) + 1]] <- row
    }
  }
  out
}

# ----------------------------------------------------------------------------
# Athlete biographical info ("infobox" in the original rgriff23 R code)
# ----------------------------------------------------------------------------

HEIGHT_RE <- "(\\d{2,3})\\s*cm"
WEIGHT_RE <- "(\\d{2,3}(?:\\.\\d+)?)\\s*kg"
BORN_RE   <- "(\\d{1,2})\\s+([A-Za-z]+)\\s+(\\d{4})"

parse_athlete_bio <- function(soup) {
  bio <- list(sex = NA_character_, height = NA_real_,
              weight = NA_real_, born = NULL)

  info <- soup %>% html_element("table.biodata")
  if (is.na(info)) info <- soup %>% html_element("table")
  if (is.na(info)) return(bio)

  for (tr in info %>% html_elements("tr")) {
    cells <- tr %>% html_elements("th, td")
    if (length(cells) < 2) next

    label <- html_text2(cells[[1]]) %>% str_replace(":$", "") %>% tolower()
    value <- html_text2(cells[[2]])

    if (str_starts(label, "sex") || str_starts(label, "gender")) {
      bio$sex <- if (str_detect(tolower(value), "female")) "F" else "M"
    } else if (str_detect(label, "height")) {
      m <- str_match(value, HEIGHT_RE)
      if (!is.na(m[1, 2])) bio$height <- as.numeric(m[1, 2])
    } else if (str_detect(label, "weight")) {
      m <- str_match(value, WEIGHT_RE)
      if (!is.na(m[1, 2])) bio$weight <- as.numeric(m[1, 2])
    } else if (str_detect(label, "born")) {
      m <- str_match(value, BORN_RE)
      if (!is.na(m[1, 1])) {
        bio$born <- list(day   = as.integer(m[1, 2]),
                         month = m[1, 3],
                         year  = as.integer(m[1, 4]))
      }
    }
  }
  bio
}

MONTHS <- c(january   = 1L, february = 2L, march    = 3L, april    = 4L,
            may       = 5L, june     = 6L, july     = 7L, august   = 8L,
            september = 9L, october  = 10L, november = 11L, december = 12L)

age_at_games <- function(born, year, season) {
  if (is.null(born)) return(NA_integer_)
  bmon <- MONTHS[tolower(born$month)]
  if (is.na(bmon)) return(NA_integer_)

  ref_mon <- if (season == "Winter") 2L else 7L
  ref_day <- if (season == "Winter") 1L else 15L

  age <- year - born$year
  if (bmon > ref_mon || (bmon == ref_mon && born$day > ref_day)) {
    age <- age - 1L
  }
  if (age >= 8 && age <= 100) as.integer(age) else NA_integer_
}

infer_sex_from_event <- function(event_name) {
  if (str_detect(tolower(event_name), "women|girls")) "F" else "M"
}

# ----------------------------------------------------------------------------
# Per-edition scrape
# ----------------------------------------------------------------------------

scrape_edition <- function(fetcher, edition_id, starting_id, bio_cache) {
  edition_soup <- fetcher$get(sprintf("/editions/%d", edition_id))
  meta         <- parse_edition(edition_soup, edition_id)
  message(sprintf("  -> %d %s (%s)", meta$year, meta$season, meta$city))

  sports <- list_sports(edition_soup)
  message(sprintf("     %d sports / disciplines", nrow(sports)))

  rows    <- list()
  next_id <- starting_id

  for (i in seq_len(nrow(sports))) {
    sport_name <- sports$name[i]
    sport_path <- sports$href[i]

    sport_soup <- tryCatch(fetcher$get(sport_path), error = function(e) NULL)
    if (is.null(sport_soup)) next
    events <- list_events_for_sport(sport_soup)
    message(sprintf("     %s: %d events", sport_name, nrow(events)))

    for (j in seq_len(nrow(events))) {
      event_name <- events$name[j]
      event_path <- events$href[j]

      ev_soup <- tryCatch(fetcher$get(event_path), error = function(e) NULL)
      if (is.null(ev_soup)) next
      results <- parse_results_table(ev_soup)

      for (r in results) {
        ath_path <- r$athlete_path
        if (is.null(ath_path) || is.na(ath_path) || ath_path == "") next

        # Cache athlete bios across editions; many athletes compete more than once
        if (!exists(ath_path, envir = bio_cache, inherits = FALSE)) {
          bio_soup <- tryCatch(fetcher$get(ath_path), error = function(e) NULL)
          bio <- if (is.null(bio_soup)) {
            list(sex = NA_character_, height = NA_real_,
                 weight = NA_real_, born = NULL)
          } else {
            parse_athlete_bio(bio_soup)
          }
          assign(ath_path, bio, envir = bio_cache)
        }
        bio <- get(ath_path, envir = bio_cache)

        sex_val  <- bio$sex %||% infer_sex_from_event(event_name)
        team_raw <- r$noc %||% r$team %||% ""
        noc_val  <- toupper(substr(r$noc %||% "", 1, 3))

        # Match the original rgriff23 ASCII-folding step exactly
        name_clean <- iconv(r$athlete %||% "", from = "UTF-8",
                            to = "ASCII", sub = "")
        team_clean <- iconv(team_raw, from = "UTF-8",
                            to = "ASCII", sub = "")

        rows[[length(rows) + 1]] <- tibble(
          ID     = next_id,
          Name   = str_trim(name_clean),
          Sex    = sex_val,
          Age    = age_at_games(bio$born, meta$year, meta$season),
          Height = bio$height,
          Weight = bio$weight,
          Team   = str_trim(team_clean),
          NOC    = noc_val,
          Games  = meta$games_label,
          Year   = meta$year,
          Season = meta$season,
          City   = meta$city,
          Sport  = sport_name,
          Event  = paste(sport_name, event_name),
          Medal  = r$medal
        )
        next_id <- next_id + 1L
      }
    }
  }

  list(rows = bind_rows(rows), next_id = next_id)
}

# ----------------------------------------------------------------------------
# CSV writer (matches rgriff23 NA-as-"NA" convention)
# ----------------------------------------------------------------------------

write_csv_na <- function(df, path) {
  if (nrow(df) == 0) {
    # Still produce a valid header-only file for empty edition results
    df <- tibble(!!!setNames(rep(list(character()), length(COLUMNS)), COLUMNS))
  }
  write.csv(df[, COLUMNS], path, row.names = FALSE, na = "NA")
}

# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

main <- function() {
  option_list <- list(
    make_option("--editions",    type = "character", default = NULL,
                help = "Olympedia edition IDs, comma- or space-separated"),
    make_option("--out",         type = "character", default = NULL,
                help = "Output CSV path"),
    make_option("--checkpoints", type = "character", default = "./checkpoints",
                help = "Directory for per-edition CSV checkpoints [%default]"),
    make_option("--delay",       type = "double",    default = DEFAULT_DELAY,
                help = "Seconds between HTTP requests [%default]"),
    make_option("--starting",    type = "integer",   default = 1000000L,
                help = "Starting ID for new athlete-event rows [%default]")
  )
  opts <- parse_args(OptionParser(option_list = option_list))

  if (is.null(opts$editions) || is.null(opts$out)) {
    stop("--editions and --out are required")
  }
  editions <- as.integer(strsplit(opts$editions, "[,[:space:]]+")[[1]])
  editions <- editions[!is.na(editions)]
  if (length(editions) == 0) stop("No valid edition IDs parsed from --editions")

  dir.create(opts$checkpoints, recursive = TRUE, showWarnings = FALSE)
  fetcher   <- make_fetcher(delay = opts$delay)
  bio_cache <- new.env(hash = TRUE, parent = emptyenv())
  all_rows  <- list()
  next_id   <- opts$starting

  for (ed in editions) {
    message(sprintf("\n=== Edition %d ===", ed))
    res <- scrape_edition(fetcher, ed, next_id, bio_cache)
    all_rows[[length(all_rows) + 1]] <- res$rows
    next_id <- res$next_id

    ckpt <- file.path(opts$checkpoints, sprintf("edition_%d.csv", ed))
    write_csv_na(res$rows, ckpt)
    message(sprintf("  checkpoint -> %s (%d rows)", ckpt, nrow(res$rows)))
  }

  combined <- bind_rows(all_rows)
  write_csv_na(combined, opts$out)
  message(sprintf("\nWrote %d rows to %s", nrow(combined), opts$out))
}

if (sys.nframe() == 0) main()
