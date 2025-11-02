"""
Load test for Callva API - Phased load testing with per-request-type users.

Architecture:
- Phase 1: CreateCallUser creates 350 calls per org, then stops
- Phase 2: Other users (Update, ReadScheduled, ReadHeavy) start making requests after their org has 350 calls

To spawn 10 concurrent users per request type, use: 10 * 4 = 40 total users
- 10 CreateCallUser (active in Phase 1, then skip)
- 10 UpdateCallUser (blocked until Phase 1 complete for their org)
- 10 ReadScheduledUser (blocked + throttled 30 sec per user)
- 10 ReadHeavyUser (blocked until Phase 1 complete for their org)

To use this file:
    locust -f locustfile_callva.py --web-host 0.0.0.0 --web-port 80
"""

import random
import logging
import time
import threading
from datetime import datetime, timedelta
from locust import HttpUser, task, between

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# GLOBAL SHARED STATE
# ============================================================================

# API host (shared across all users)
API_HOST = "https://staging.api.callva.one"

# API credentials (shared across all users)
API_CREDENTIALS = [
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
NAMES = [
    "Lisa Anderson", "John Smith", "Maria Garcia", "James Wilson",
    "Anna Rodriguez", "Robert Brown", "Emma Martinez", "Michael Davis",
    "Sophia Lopez", "David Johnson", "Olivia Williams", "Daniel Miller"
]

# Shared storage for created call IDs (shared across all users)
created_call_ids = []

# Track calls created per org (max 350 per org) - gates Phase 2 users
calls_created_per_org = {
    "LoadTest1": 0,
    "LoadTest2": 0,
    "LoadTest3": 0,
}
MAX_CALLS_PER_ORG = 350

# Thread-safe lock for counter updates
org_lock = threading.Lock()


# ============================================================================
# PHASE 1: CREATE CALLS (Ramp-up)
# ============================================================================

class CreateCallUser(HttpUser):
    """
    Phase 1: Creates calls until 350 per org, then stops.
    This user runs during ramp-up and gates Phase 2 users.
    """

    host = API_HOST
    wait_time = between(0.5, 2)  # Aggressive creation

    def on_start(self):
        """Randomly assign API credentials to distribute load"""
        creds = random.choice(API_CREDENTIALS)
        self.api_token = creds["token"]
        self.org_name = creds["org"]
        self.my_call_ids = []
        logger.info(f"[CREATE] User started with org: {self.org_name}")

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    def _generate_call_data(self):
        """Generate realistic call data"""
        today = datetime.now().date()
        call_at = datetime.combine(today, datetime.min.time())

        return {
            "name": random.choice(NAMES),
            "phone": f"+1555{random.randint(0, 9):01d}{random.randint(100, 999):03d}",
            "call_at": call_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "times_called": 0,
            "provider": "Vapi",
            "status": "scheduled"
        }

    @task
    def create_call(self):
        """CREATE: Add calls until org reaches 350, then skip forever"""
        # Check if this org has reached the limit
        with org_lock:
            if calls_created_per_org[self.org_name] >= MAX_CALLS_PER_ORG:
                # Silently skip - ramp-up phase complete for this org
                return

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
                    call_id = data.get("id") or data.get("call_id") or data.get("data", {}).get("id")

                    if call_id:
                        # Increment counter for this org
                        with org_lock:
                            calls_created_per_org[self.org_name] += 1
                            count = calls_created_per_org[self.org_name]

                        # Store globally for all users to update
                        created_call_ids.append(call_id)
                        self.my_call_ids.append(call_id)

                        # Log progress every 50 calls
                        if count % 50 == 0:
                            logger.info(f"[{self.org_name}] Created {count}/{MAX_CALLS_PER_ORG} calls")

                        # Log completion
                        if count == MAX_CALLS_PER_ORG:
                            logger.info(f"🎯 [{self.org_name}] RAMP-UP COMPLETE - {MAX_CALLS_PER_ORG} calls created!")

                        response.success()
                    else:
                        logger.error(f"[{self.org_name}] CREATE: No ID in response - Status: {response.status_code}, Body: {data}")
                        response.failure(f"No ID in response: {data}")
                except Exception as e:
                    logger.error(f"[{self.org_name}] CREATE: Parse error - {e}")
                    response.failure(f"Failed to parse response: {e}")
            else:
                logger.error(f"[{self.org_name}] CREATE: Failed - Status: {response.status_code}, Body: {response.text}")
                response.failure(f"Got status {response.status_code}: {response.text[:200]}")


# ============================================================================
# PHASE 2: UPDATE CALLS (Steady-state)
# ============================================================================

class UpdateCallUser(HttpUser):
    """
    Phase 2: Updates call statuses after org reaches 350 calls.
    Blocked until ramp-up complete for this user's org.
    """

    host = API_HOST
    wait_time = between(0.5, 1.5)  # Frequent updates

    def on_start(self):
        """Randomly assign API credentials to distribute load"""
        creds = random.choice(API_CREDENTIALS)
        self.api_token = creds["token"]
        self.org_name = creds["org"]
        logger.info(f"[UPDATE] User started with org: {self.org_name}")

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    @task
    def update_call_status(self):
        """UPDATE: Change call status - only runs after 350 calls created for this org"""
        # Gate: Wait for this org to complete Phase 1
        if calls_created_per_org[self.org_name] < MAX_CALLS_PER_ORG:
            return  # Skip - Phase 1 not complete yet

        # Check if any calls exist
        if not created_call_ids:
            return  # Skip - no calls to update

        # Pick a random call ID to update
        call_id = random.choice(created_call_ids)

        # Pick a realistic status transition
        new_status = random.choice(["in_progress", "complete", "failed", "starting"])

        update_data = {"status": new_status}

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
                logger.warning(f"[{self.org_name}] UPDATE: Call not found - ID: {call_id}")
                response.failure("Call not found (404)")
            else:
                logger.error(f"[{self.org_name}] UPDATE: Failed - Status: {response.status_code}, Body: {response.text}")
                response.failure(f"Got status {response.status_code}: {response.text[:200]}")


# ============================================================================
# PHASE 2: READ SCHEDULED CALLS (Steady-state, throttled)
# ============================================================================

class ReadScheduledUser(HttpUser):
    """
    Phase 2: Reads scheduled calls after org reaches 350 calls.
    Blocked until ramp-up complete + throttled to 30 sec per individual user.
    """

    host = API_HOST
    wait_time = between(0.5, 1)  # Check frequently, but throttled internally

    def on_start(self):
        """Randomly assign API credentials to distribute load"""
        creds = random.choice(API_CREDENTIALS)
        self.api_token = creds["token"]
        self.org_name = creds["org"]
        self.last_execution = 0  # Per-user throttle timer
        logger.info(f"[READ_SCHEDULED] User started with org: {self.org_name}")

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    @task
    def read_calls_scheduled(self):
        """READ: Fetch scheduled calls - throttled to 30 sec per user"""
        # Gate: Wait for this org to complete Phase 1
        if calls_created_per_org[self.org_name] < MAX_CALLS_PER_ORG:
            return  # Skip - Phase 1 not complete yet

        # Throttle: 30 seconds per individual user
        current_time = time.time()
        if current_time - self.last_execution < 30:
            return  # Skip - too soon since last execution

        self.last_execution = current_time

        # Generate query parameters
        call_at_gt = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "status": "scheduled",
            "times_called_lt": "3",
            "per_page": "10",
            "call_at_gt": call_at_gt
        }

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
                logger.error(f"[{self.org_name}] READ_SCHEDULED: Failed - Status: {response.status_code}, Body: {response.text}")
                response.failure(f"Got status {response.status_code}: {response.text[:200]}")


# ============================================================================
# PHASE 2: READ HEAVY LOAD (Steady-state)
# ============================================================================

class ReadHeavyUser(HttpUser):
    """
    Phase 2: Heavy load queries after org reaches 350 calls.
    Blocked until ramp-up complete for this user's org.
    """

    host = API_HOST
    wait_time = between(3, 3)  # Run every 3 seconds

    def on_start(self):
        """Randomly assign API credentials to distribute load"""
        creds = random.choice(API_CREDENTIALS)
        self.api_token = creds["token"]
        self.org_name = creds["org"]
        logger.info(f"[READ_HEAVY] User started with org: {self.org_name}")

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    @task
    def read_calls_heavy_load(self):
        """READ: Heavy load query - only runs after 350 calls created for this org"""
        # Gate: Wait for this org to complete Phase 1
        if calls_created_per_org[self.org_name] < MAX_CALLS_PER_ORG:
            return  # Skip - Phase 1 not complete yet

        # Get start and end of today
        today = datetime.now().date()
        start_of_day = datetime.combine(today, datetime.min.time()).isoformat() + "Z"
        end_of_day = datetime.combine(today, datetime.max.time()).isoformat() + "Z"

        params = {
            "call_at_gte": start_of_day,
            "call_at_lte": end_of_day,
            "per_page": "500",
            "page": "1",
            "sort": "-last_call_time"
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])

        with self.client.get(
            f"/api/v1/external/calls?{query_string}",
            headers=self._get_headers(),
            catch_response=True,
            name="GET /api/v1/external/calls [Heavy Load Query]"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                logger.error(f"[{self.org_name}] READ_HEAVY: Failed - Status: {response.status_code}, Body: {response.text}")
                response.failure(f"Got status {response.status_code}: {response.text[:200]}")
