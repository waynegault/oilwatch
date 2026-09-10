from selenium import webdriver

# Path to the downloaded Edge WebDriver executable
edge_driver_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedgedriver.exe"

# Initialize the Edge WebDriver
driver = webdriver.Edge(executable_path=edge_driver_path)

# Navigate to the desired URL
url = "https://connon.fuelsoft.co.uk/WEBPLUS/Pages/WebOrdering/OnlineQuote.aspx"
driver.get(url)

# Perform actions (e.g., fill in form fields, click buttons, etc.)
# ...

# Close the browser
driver.quit()
