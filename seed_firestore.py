import os
import subprocess
from google.cloud import firestore
from google.oauth2 import credentials

PROJECT_ID = "qwiklabs-gcp-03-1463c3fe44c8"

PLANTS_DATA = [
    {
        "id": "monstera_deliciosa",
        "name": "Monstera Deliciosa",
        "species": "Monstera deliciosa",
        "category": "Tropical Indoor",
        "watering_frequency_days": 7,
        "sunlight_requirement": "Bright indirect light",
        "care_difficulty": "Easy",
        "last_watered_date": "2026-09-20",
        "notes": "Prefers humid environments. Wipe leaves to keep dust-free.",
    },
    {
        "id": "fiddle_leaf_fig",
        "name": "Fiddle Leaf Fig",
        "species": "Ficus lyrata",
        "category": "Foliage Tree",
        "watering_frequency_days": 10,
        "sunlight_requirement": "Bright direct to indirect light",
        "care_difficulty": "Moderate",
        "last_watered_date": "2026-09-18",
        "notes": "Sensitive to drafts and sudden changes in position.",
    },
    {
        "id": "snake_plant",
        "name": "Snake Plant",
        "species": "Sansevieria trifasciata",
        "category": "Succulent",
        "watering_frequency_days": 14,
        "sunlight_requirement": "Low to bright indirect light",
        "care_difficulty": "Very Easy",
        "last_watered_date": "2026-09-15",
        "notes": "Extremely drought-tolerant. Allow soil to dry out completely between waterings.",
    },
    {
        "id": "peace_lily",
        "name": "Peace Lily",
        "species": "Spathiphyllum",
        "category": "Flowering Indoor",
        "watering_frequency_days": 5,
        "sunlight_requirement": "Medium to low indirect light",
        "care_difficulty": "Easy",
        "last_watered_date": "2026-09-25",
        "notes": "Tells you when it needs water by drooping slightly.",
    },
]


def get_firestore_client() -> firestore.Client:
    """Gets Firestore client with hardcoded project ID and local token fallback if needed."""
    try:
        db = firestore.Client(project=PROJECT_ID)
        # Test query to verify credentials
        db.collection("plants").limit(1).get()
        return db
    except Exception:
        try:
            token = (
                subprocess.check_output(["gcloud", "auth", "print-access-token"])
                .decode()
                .strip()
            )
            creds = credentials.Credentials(token)
            return firestore.Client(project=PROJECT_ID, credentials=creds)
        except Exception:
            return firestore.Client(project=PROJECT_ID)


def seed():
    print(f"Connecting to Firestore for project: {PROJECT_ID}...")
    db = get_firestore_client()
    collection_ref = db.collection("plants")

    for plant in PLANTS_DATA:
        plant_id = plant["id"]
        collection_ref.document(plant_id).set(plant)
        print(f"Seeded plant: {plant_id} ({plant['name']})")

    print("Successfully seeded Firestore plants collection!")


if __name__ == "__main__":
    seed()
