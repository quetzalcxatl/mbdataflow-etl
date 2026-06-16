### \# MBDataflow

MBDataflow is a Python project designed to automate the process of scraping data from multiple sources and loading it into Google BigQuery. Its primary purpose is to help monitor the quality of the Metrobús service.

-----

### Features

  * **Data Scraping**: Scrapes and downloads CSV files from various data sources.
  * **Modular Connectors**: Each data source is handled by a dedicated connector with its own logic for scraping, transforming, and loading data.
  * **Automated Execution**: Can be run to execute all available connectors at once.
  * **Extensible Design**: The architecture makes it easy to add new data sources without altering the core codebase.
  * **Included Connector**: A pre-built connector for the Sonda PV portal is included to get you started.

-----

### Getting Started

#### Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/your-org/MBDataflow.git
    cd MBDataflow
    ```
2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

#### Configuration

  * **Google Cloud**: The project uses **Workload Identity** for authentication in a cloud environment. For local testing, you must set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable to the path of your service account key file.
  * **Project Settings**: The `config.py` file contains the BigQuery dataset and table IDs. Update these to match your project.
  * **Sonda PV**: The file `mbdataflow/sonda_pv_config.json` allows you to configure the specific times for the Sonda PV data download.

#### Usage

  * To run all available connectors:
    ```bash
    python -m mbdataflow
    ```
  * To run a single connector (e.g., Sonda PV):
    ```bash
    python -m mbdataflow --connector Sonda_PV
    ```
  * To see all available command-line options, use the help flag:
    ```bash
    python -m mbdataflow --help
    ```

-----

### Cloud Deployment

The recommended way to deploy and run this project in Google Cloud is using **Cloud Run Jobs** and **Cloud Scheduler**.

1.  **Containerize**: Use the provided `Dockerfile` to create a container image of your application.
2.  **Deploy**: Deploy the container to Cloud Run Jobs, linking it to a service account with the appropriate permissions (e.g., BigQuery Data Editor). This enables Workload Identity, removing the need for a service account key file in the cloud.
3.  **Schedule**: Use Cloud Scheduler to trigger the Cloud Run Job on a set schedule (e.g., daily at 2:00 AM) using a cron expression.

-----

### Contributing

We welcome contributions\! To contribute:

1.  Fork the repository.
2.  Create a new feature branch.
3.  Implement your changes (e.g., a new connector in the `mbdataflow/connectors/` directory).
4.  Submit a pull request.

-----

### License

This project is licensed under the MIT License.