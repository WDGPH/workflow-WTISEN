# WTISEN Data Pipeline

## Introduction
These containers are used as part of a data pipeline to automatically retrieve and process data from Public Health Ontario's (PHO) Water Testing Information System Electronic Notification (WTISEN). Containerization of these data pipeline components offers environment isolation and reproducibility. Below follows a description and basic usage of each container. 

Container images are built by Github actions, and pushed to Github's container registry. You can find up-to-date built images [here](https://github.com/orgs/WDGPH/packages?repo_name=workflow-WTISEN).

## Retrieval Container
This container utilizes [Selenium](https://www.selenium.dev/) to automate browser authentication through PHO's login portal to WTISEN. Once authenticated, the browser session is passed to Python's [Requests](https://requests.readthedocs.io/en/latest/) library to download a report with the parameters specified to the container. The retrieved file is in CSV format. Data can be retrieved for up to 3 years at a time.

To use, `WTISEN_USER` and `WTISEN_PASSWORD` environment variables must be set for the container (login email and password to WTISEN respectively). It is strongly suggested that secure key vault is utilized for this process and that credentials are rotated frequently. Additionally, the following arguments are required:

**1. `url`**  
URL to access WTISEN  
**Example**: `https://example.com/collaboration/sites/wtisen`

**2. `phu`**  
Four-digit Public Health Unit ID  
**Example**: `1234`

**3. `report`**
Report name, preceded in report URL by `/RSReports/` and ends with `.rdl`  
**Example**: `Water+Testing+PHU+Report.rdl`

**4. `start`**  
The start date for the records you want to retrieve in the format of `YYYY-MM-DD`  
**Example**: `2022-01-01`

**5. `end`**  
The end date for the records you want to retrieve in the format of `YYYY-MM-DD`  
**Example**: `2022-12-31`

**6. `output`**  
The filename where the output will be written in CSV format  
**Example**: `wtisen.csv`

## Processing Container
This container utilizes [R](https://www.r-project.org/) to process and standardize CSV file retrieved in the previous container. The output is a [parquet file](https://parquet.apache.org/). This format preserves column type information which can simplify importing the data into other tools. Use of [renv](https://rstudio.github.io/renv/) ensures a reproducible environment.

The following arguments are required:

**1. `input`**  
The path of the file downloaded from WTISEN (CSV format)  
**Example**: `wtisen.csv`

**2. `output`**  
The path of the processed output (parquet format)  
**Example**: `wtisen_processed.parquet`

## Additionally Required Containers
Usage of this pipeline may require additional components to determine the date range for which you wish to retrieve data (to be passed to the data retrieval component), as well as a stage to integrate the processed data into your own data repository. Since platforms will vary for these operations, and these operations are quite basic, containers are not provided for them in this repository. 

## Pipeline Orchestration
This data pipeline can be orchestrated by a variety of tools that support containerized components, but has been developed and tested with [Kubeflow Pipelines](https://www.kubeflow.org/), which is based on [Argo Workflows](https://argoproj.github.io/argo-workflows/).

## Contributing
Browser automations often break due to changes in the websites they act on. Contributions that help to quickly flag and/or address such issues are appreciated. Dependency updates, documentation improvements, logging improvements, and additions of tests will enhance the usability and reliability of this project.