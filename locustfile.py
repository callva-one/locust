"""
Example Locust load test file for Railway deployment.
This file tests the public httpbin.org API service.

You can override the target host in the Locust web UI or via LOCUST_HOST environment variable.
"""

from locust import HttpUser, task, between


class HttpBinUser(HttpUser):
    """
    Load test user that makes requests to httpbin.org endpoints.
    """

    # Default target - can be overridden in web UI or via env var
    host = "https://httpbin.org"

    # Wait 1-3 seconds between tasks
    wait_time = between(1, 3)

    @task(3)
    def get_status(self):
        """Test GET request returning 200 status"""
        self.client.get("/status/200")

    @task(2)
    def get_headers(self):
        """Test GET request returning headers"""
        self.client.get("/headers")

    @task(1)
    def get_json(self):
        """Test GET request returning JSON"""
        self.client.get("/json")

    @task(1)
    def get_delay(self):
        """Test GET request with 1 second delay"""
        self.client.get("/delay/1")
