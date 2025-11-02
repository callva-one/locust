"""
Load test for Callva API - simulates realistic call management workflow.

This test simulates:
1. Creating calls via API
2. Reading calls back (like external system polling)
3. Updating call statuses (simulating webhook responses)

To use this file:
    locust -f locustfile_callva.py --web-host 0.0.0.0 --web-port 80
"""

import random
import logging
from datetime import datetime, timedelta
from locust import HttpUser, task, between
import json

# Configure logging to see detailed error responses
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Shared storage for created call IDs (shared across all users)
created_call_ids = []


class CallvaUser(HttpUser):
    """
    Simulates a user/system interacting with the Callva API.
    Models the realistic workflow of creating, reading, and updating calls.
    """

    host = "https://staging.api.callva.one"

    # Wait 0.5-2 seconds between actions (aggressive load testing)
    wait_time = between(0.5, 2)

    # Multiple API keys with org names to distribute load across tenants and avoid rate limits
    api_credentials = [
        {
            "org": "LoadTest1",
            "token": "VrfzWbYWeBy04CYErHIsGa1TNW3rkRteMBGZgcZ9bMnGYcAHVmrufOpuyY9eyemTRkNQRNG8dqV2wTHd"
        },
        {
            "org": "LoadTest2",
            "token": "FTK5d9sBRiFroAN3fUTMoTW9w5GVaBDuiL2xrYRyEKwTOHCt8uAOi34o2TnWRsepUTtH2sYNw0z8PrZe"
        },
        {
            "org": "LoadTest3",
            "token": "nNOOIJawblzHBKuQHsXTCFy8BiOWnTaj0NJ6YnYIn32moBZ7LQc2NGHfeZAT5NyrAr76UFdAKdHaU1Fl"
        },
    ]

    # Sample data for generating realistic calls
    names = [
        "Lisa Anderson", "John Smith", "Maria Garcia", "James Wilson",
        "Anna Rodriguez", "Robert Brown", "Emma Martinez", "Michael Davis",
        "Sophia Lopez", "David Johnson", "Olivia Williams", "Daniel Miller"
    ]

    def on_start(self):
        """Called when a user starts - randomly assign API credentials to distribute load"""
        self.my_call_ids = []  # Track this user's created calls
        creds = random.choice(self.api_credentials)  # Each user gets one of 3 orgs
        self.api_token = creds["token"]
        self.org_name = creds["org"]
        logger.info(f"User started with org: {self.org_name}")

    def _get_headers(self):
        """Generate request headers with auth"""
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    def _generate_call_data(self):
        """Generate realistic call data with random variations"""
        # call_at: Current date at 00:00:00
        today = datetime.now().date()
        call_at = datetime.combine(today, datetime.min.time())

        return {
            "name": random.choice(self.names),
            "phone": f"+1555{random.randint(0, 9):01d}{random.randint(100, 999):03d}",
            "call_at": call_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "times_called": 0,
            "provider": "Vapi",
            "status": "scheduled"
        }

    @task(3)
    def create_call(self):
        """
        CREATE: Add a new call to the system.
        Weight: 3 (~33% of requests)
        """
        call_data = self._generate_call_data()

        with self.client.post(
            "/api/v1/external/calls",
            headers=self._get_headers(),
            json=call_data,
            catch_response=True,
            name="POST /api/v1/external/calls [Create Call]"
        ) as response:
            if response.status_code == 201 or response.status_code == 200:
                try:
                    data = response.json()
                    # Extract call ID from response (adjust based on your API response structure)
                    call_id = data.get("id") or data.get("call_id") or data.get("data", {}).get("id")

                    if call_id:
                        # Store globally for all users to update
                        created_call_ids.append(call_id)
                        # Store locally for this user
                        self.my_call_ids.append(call_id)
                        response.success()
                    else:
                        logger.error(f"[{self.org_name}] CREATE: No ID in response - Status: {response.status_code}, Body: {data}")
                        response.failure(f"No ID in response: {data}")
                except Exception as e:
                    logger.error(f"[{self.org_name}] CREATE: Parse error - Status: {response.status_code}, Body: {response.text}, Error: {e}")
                    response.failure(f"Failed to parse response: {e}")
            else:
                logger.error(f"[{self.org_name}] CREATE: Failed - Status: {response.status_code}, Body: {response.text}")
                response.failure(f"Got status {response.status_code}: {response.text[:200]}")

    @task(3)
    def read_calls_scheduled(self):
        """
        READ: Fetch scheduled calls (simulates external system polling).
        Weight: 3 (~33% of requests)
        """
        # Generate query parameters for realistic filtering
        call_at_gt = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "status": "scheduled",
            "times_called_lt": "3",
            "per_page": "10",
            "call_at_gt": call_at_gt
        }

        # Build query string
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])

        with self.client.get(
            f"/api/v1/external/calls?{query_string}",
            headers=self._get_headers(),
            catch_response=True,
            name="GET /api/v1/external/calls [Read Scheduled]"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                logger.error(f"[{self.org_name}] READ SCHEDULED: Failed - Status: {response.status_code}, Body: {response.text}")
                response.failure(f"Got status {response.status_code}: {response.text[:200]}")

    @task(2)
    def update_call_status(self):
        """
        UPDATE: Change call status (simulates webhook/workflow updates).
        Weight: 2 (~22% of requests)
        """
        # Use globally created IDs if available, fallback to user's own
        call_ids = created_call_ids if created_call_ids else self.my_call_ids

        if not call_ids:
            # Skip if no calls created yet
            return

        # Pick a random call ID to update
        call_id = random.choice(call_ids)

        # Pick a realistic status transition
        new_status = random.choice(["in_progress", "complete", "failed", "starting"])

        update_data = {
            "status": new_status
        }

        with self.client.put(
            f"/api/v1/external/calls/{call_id}",
            headers=self._get_headers(),
            json=update_data,
            catch_response=True,
            name="PUT /api/v1/external/calls/{id} [Update Status]"
        ) as response:
            if response.status_code in [200, 204]:
                response.success()
            elif response.status_code == 404:
                # Call might have been deleted, remove from our list
                if call_id in self.my_call_ids:
                    self.my_call_ids.remove(call_id)
                logger.warning(f"[{self.org_name}] UPDATE: Call not found - ID: {call_id}, Status: 404")
                response.failure("Call not found (404)")
            else:
                logger.error(f"[{self.org_name}] UPDATE: Failed - ID: {call_id}, Status: {response.status_code}, Body: {response.text}")
                response.failure(f"Got status {response.status_code}: {response.text[:200]}")


# Additional user class for read-heavy workload (simulates external system)
class ExternalSystemUser(HttpUser):
    """
    Simulates the external system that only polls for calls.
    This creates a more realistic mixed workload.
    """

    host = "https://staging.api.callva.one"
    wait_time = between(2, 5)  # Slower polling

    # Multiple API keys with org names to distribute load across tenants
    api_credentials = [
        {
            "org": "LoadTest1",
            "token": "VrfzWbYWeBy04CYErHIsGa1TNW3rkRteMBGZgcZ9bMnGYcAHVmrufOpuyY9eyemTRkNQRNG8dqV2wTHd"
        },
        {
            "org": "LoadTest2",
            "token": "FTK5d9sBRiFroAN3fUTMoTW9w5GVaBDuiL2xrYRyEKwTOHCt8uAOi34o2TnWRsepUTtH2sYNw0z8PrZe"
        },
        {
            "org": "LoadTest3",
            "token": "nNOOIJawblzHBKuQHsXTCFy8BiOWnTaj0NJ6YnYIn32moBZ7LQc2NGHfeZAT5NyrAr76UFdAKdHaU1Fl"
        },
    ]

    def on_start(self):
        """Randomly assign API credentials to distribute load"""
        creds = random.choice(self.api_credentials)
        self.api_token = creds["token"]
        self.org_name = creds["org"]
        logger.info(f"ExternalSystemUser started with org: {self.org_name}")

    @task
    def read_calls_heavy_load(self):
        """Heavy load query with large result set, grouping, and sorting"""
        # Get start and end of today
        today = datetime.now().date()
        start_of_day = datetime.combine(today, datetime.min.time()).isoformat() + "Z"
        end_of_day = datetime.combine(today, datetime.max.time()).isoformat() + "Z"

        params = {
            "call_at_gte": start_of_day,
            "call_at_lte": end_of_day,
            "per_page": "500",
            "page": "1",
            "sort": "-last_call_time",
            "group": "doctor_name"
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])

        self.client.get(
            f"/api/v1/external/calls?{query_string}",
            headers={
                "Authorization": f"Bearer {self.api_token}"
            },
            name="GET /api/v1/external/calls [Heavy Load Query]"
        )
