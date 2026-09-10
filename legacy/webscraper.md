1. Scraping Oil Prices from Supplier Websites:

Identify the heating oil supplier websites you want to scrape.
Inspect the structure of these websites to find where the oil prices are listed.
Use Python libraries like requests or BeautifulSoup to fetch the web pages and extract the relevant price information.

2.Data Storage:

Store the scraped data in a structured format (e.g., CSV, JSON, or a database).
Include the supplier name, date, and oil price in your data records.

3. Daily Automation:

Set up a daily task to run your scraping script.
You can use the built-in Windows Task Scheduler or create a Python script that runs periodically using a scheduler library like schedule.

4. Data Analysis and Visualization:

Once you have accumulated enough data, analyze it to identify trends and calculate the lowest-cost supplier.
Use Python libraries like pandas for data manipulation and matplotlib or seaborn for creating time series charts.

5. Creating the Time Series Chart:

Plot the oil prices over time using the data you’ve collected.
Highlight the lowest-cost supplier for each day.

6. Setting Up Automation on Your PC:

Since you’re using Visual Studio Code (VS Code), you can create a Python script and save it as a .py file.
Use the Windows Task Scheduler to run your script daily at a specific time.
In Task Scheduler, create a new task, set the trigger to daily, and specify the path to your Python script.
