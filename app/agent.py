# ruff: noqa
import base64
import datetime
import json
import os
import subprocess
import urllib.parse
import urllib.request
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types
from google.oauth2 import credentials

from .a2ui_utils import a2ui_callback

load_dotenv()

PROJECT_ID = "qwiklabs-gcp-03-1463c3fe44c8"
BUCKET_NAME = "smart-greenhouse-assets-44718162815"
MEMORY_BANK_ID = "608678642020515840"


def _get_db() -> firestore.Client:
    """Helper to initialize Firestore client with hardcoded project ID and local token fallback."""
    try:
        db = firestore.Client(project=PROJECT_ID)
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


def _get_code_executor() -> AgentEngineSandboxCodeExecutor:
    """Helper to initialize AgentEngineSandboxCodeExecutor using deployment_metadata.json if available."""
    agent_engine_id = None
    metadata_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as f:
                data = json.load(f)
                agent_engine_id = data.get("remote_agent_runtime_id")
        except Exception:
            pass

    return AgentEngineSandboxCodeExecutor(agent_engine_resource_name=agent_engine_id)


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback triggered after each turn to extract and persist long-term memories to Memory Bank."""
    await callback_context.add_session_to_memory()
    return None


def memory_service_builder() -> VertexAiMemoryBankService:
    """Configures the Memory Bank service for deployed containers or local runner instantiation."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


def list_plants(category: str = "") -> str:
    """Lists all plants in the greenhouse catalog from Firestore, optionally filtered by category.

    Args:
        category: Optional category filter (e.g. 'Tropical Indoor', 'Succulent', 'Foliage Tree').

    Returns:
        JSON string containing the list of matching plants.
    """
    db = _get_db()
    docs = db.collection("plants").stream()
    plants = []
    for doc in docs:
        data = doc.to_dict()
        if category:
            if category.lower() in data.get("category", "").lower():
                plants.append(data)
        else:
            plants.append(data)
    return json.dumps(plants, indent=2)


def get_plant_details(plant_id: str) -> str:
    """Gets details for a specific plant from Firestore by its ID.

    Args:
        plant_id: The ID of the plant (e.g. 'monstera_deliciosa', 'snake_plant').

    Returns:
        JSON string with plant details or error message if not found.
    """
    db = _get_db()
    doc_ref = db.collection("plants").document(plant_id)
    doc = doc_ref.get()
    if doc.exists:
        return json.dumps(doc.to_dict(), indent=2)
    return f"Plant with ID '{plant_id}' not found."


def log_plant_care(plant_id: str, last_watered_date: str, notes: str = "") -> str:
    """Logs care activities (such as watering or fertilizing) for a plant in Firestore.

    Args:
        plant_id: The ID of the plant.
        last_watered_date: Date formatted as YYYY-MM-DD.
        notes: Optional care notes or observations.

    Returns:
        Success or error message string.
    """
    db = _get_db()
    doc_ref = db.collection("plants").document(plant_id)
    doc = doc_ref.get()
    if not doc.exists:
        return f"Plant '{plant_id}' does not exist in the database."

    update_data = {"last_watered_date": last_watered_date}
    if notes:
        existing_notes = doc.to_dict().get("notes", "")
        update_data["notes"] = f"{existing_notes} [{last_watered_date}: {notes}]".strip()

    doc_ref.update(update_data)
    return f"Successfully updated care log for plant '{plant_id}'."


def add_plant(
    plant_id: str,
    name: str,
    species: str,
    category: str,
    watering_frequency_days: int = 7,
    sunlight_requirement: str = "Indirect sunlight",
    care_difficulty: str = "Easy",
    notes: str = "",
) -> str:
    """Adds a new plant to the greenhouse catalog in Firestore.

    Args:
        plant_id: Unique identifier for the plant (lowercase string, e.g. 'pothos').
        name: Common name of the plant (e.g. 'Golden Pothos').
        species: Botanical species name.
        category: Category classification (e.g. 'Indoor Vine').
        watering_frequency_days: Number of days between waterings.
        sunlight_requirement: Sunlight needs description.
        care_difficulty: Care difficulty level ('Easy', 'Moderate', 'Hard').
        notes: Any additional notes on plant care.

    Returns:
        Success message string.
    """
    db = _get_db()
    plant_data = {
        "id": plant_id,
        "name": name,
        "species": species,
        "category": category,
        "watering_frequency_days": watering_frequency_days,
        "sunlight_requirement": sunlight_requirement,
        "care_difficulty": care_difficulty,
        "last_watered_date": "Not specified",
        "notes": notes,
    }
    db.collection("plants").document(plant_id).set(plant_data)
    return f"Successfully added plant '{name}' ({plant_id}) to Firestore."


def check_watering_schedule_alerts() -> str:
    """Checks all plants in the Firestore catalog and returns alerts for plants that are due or overdue for watering.

    Returns:
        JSON string listing plants that need watering, including care status and dates.
    """
    db = _get_db()
    docs = db.collection("plants").stream()
    today = datetime.date.today()
    alerts = []

    for doc in docs:
        plant = doc.to_dict()
        last_watered_str = plant.get("last_watered_date")
        freq = plant.get("watering_frequency_days", 7)
        name = plant.get("name", plant.get("id", "Unknown plant"))

        if not last_watered_str or last_watered_str == "Not specified":
            alerts.append({"id": plant.get("id"), "name": name, "status": "No watering record"})
            continue

        try:
            last_watered = datetime.datetime.strptime(last_watered_str, "%Y-%m-%d").date()
            next_water_date = last_watered + datetime.timedelta(days=freq)
            days_diff = (today - next_water_date).days

            if days_diff >= 0:
                alerts.append({
                    "id": plant.get("id"),
                    "name": name,
                    "last_watered": last_watered_str,
                    "frequency_days": freq,
                    "next_water_date": str(next_water_date),
                    "status": "Overdue" if days_diff > 0 else "Due today",
                    "days_overdue": days_diff,
                })
        except ValueError:
            alerts.append({"id": plant.get("id"), "name": name, "status": "Invalid date format"})

    if not alerts:
        return "All plants are well-watered! No watering alerts today."
    return json.dumps(alerts, indent=2)


def get_environmental_weather_data(location_name: str = "San Francisco") -> str:
    """Fetches real-time environmental weather data (temperature, humidity, rainfall) for a location using Open-Meteo API.

    Args:
        location_name: City name to fetch environmental data for (e.g. 'San Francisco', 'Austin', 'Miami', 'London').

    Returns:
        JSON string containing temperature, humidity, rainfall, and daytime metrics for greenhouse care guidance.
    """
    try:
        api_key = os.getenv("WEATHER_API_KEY")

        encoded_city = urllib.parse.quote(location_name)
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_city}&count=1"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "SmartGreenhouseAgent/1.0"})
        with urllib.request.urlopen(req) as resp:
            geo_data = json.loads(resp.read().decode())

        if not geo_data.get("results"):
            return f"Could not find location coordinates for '{location_name}'."

        loc = geo_data["results"][0]
        lat, lon = loc["latitude"], loc["longitude"]
        city_formatted = f"{loc['name']}, {loc.get('admin1', loc.get('country', ''))}"

        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,rain,is_day"
        req_w = urllib.request.Request(weather_url, headers={"User-Agent": "SmartGreenhouseAgent/1.0"})
        with urllib.request.urlopen(req_w) as resp_w:
            w_data = json.loads(resp_w.read().decode())

        current = w_data.get("current", {})
        units = w_data.get("current_units", {})

        env_summary = {
            "location": city_formatted,
            "temperature": f"{current.get('temperature_2m')} {units.get('temperature_2m', '°C')}",
            "relative_humidity": f"{current.get('relative_humidity_2m')} {units.get('relative_humidity_2m', '%')}",
            "rainfall_mm": f"{current.get('rain')} {units.get('rain', 'mm')}",
            "is_daytime": bool(current.get("is_day", 1)),
            "api_key_configured": bool(api_key),
        }
        return json.dumps(env_summary, indent=2)
    except Exception as e:
        return f"Error fetching environmental weather data: {str(e)}"


def geocode_address(address: str) -> str:
    """Uses Google Maps Geocoding API to convert an address string into latitude and longitude coordinates.

    Args:
        address: The address or place string to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        JSON string containing formatted address, latitude, and longitude.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not configured."

    try:
        encoded_addr = urllib.parse.quote(address)
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_addr}&key={api_key}"
        req = urllib.request.Request(url, headers={"User-Agent": "SmartGreenhouseAgent/1.0"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())

        if not data.get("results"):
            return f"No geocoding results found for address: '{address}'."

        result = data["results"][0]
        loc = result["geometry"]["location"]
        formatted_address = result.get("formatted_address", address)

        output = {
            "formatted_address": formatted_address,
            "location": {
                "latitude": loc["lat"],
                "longitude": loc["lng"],
            }
        }
        return json.dumps(output, indent=2)
    except Exception as e:
        return f"Geocoding failed: {str(e)}"


def find_nearby_places(latitude: float, longitude: float, place_type: str = "florist") -> str:
    """Uses Google Places API (New) searchNearby endpoint to find nearby places of a given type.

    Args:
        latitude: Latitude coordinate for search center.
        longitude: Longitude coordinate for search center.
        place_type: Type of place to search for (e.g. 'florist', 'store', 'park').

    Returns:
        JSON string with nearby places containing key fields (name, address, location).
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not configured."

    try:
        places_url = "https://places.googleapis.com/v1/places:searchNearby"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        }
        body = json.dumps({
            "includedTypes": [place_type],
            "maxResultCount": 5,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": 10000.0,
                }
            }
        }).encode("utf-8")

        req = urllib.request.Request(places_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())

        places_list = []
        for p in data.get("places", []):
            places_list.append({
                "name": p.get("displayName", {}).get("text", "Unknown"),
                "address": p.get("formattedAddress", "N/A"),
                "location": p.get("location", {}),
            })

        if not places_list:
            return f"No nearby places found of type '{place_type}'."

        return json.dumps(places_list, indent=2)
    except Exception as e:
        return f"Places API search failed: {str(e)}"


async def generate_plant_image(
    prompt: str,
    tool_context: ToolContext,
) -> str:
    """Generates an image of a plant or greenhouse item using the gemini-3.1-flash-lite-image model in the global region.
    Saves the generated image as an ADK artifact and uploads it directly from memory bytes to a public Cloud Storage bucket, returning its public URL.

    Args:
        prompt: Description of the plant or greenhouse item image to generate.

    Returns:
        The public Cloud Storage https URL of the uploaded image.
    """
    try:
        genai_client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global",
        )

        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=f"Generate a detailed, high quality photo of: {prompt}",
        )

        if not response.candidates or not response.candidates[0].content.parts:
            return "Failed to generate image: empty response from model."

        part = response.candidates[0].content.parts[0]
        if not part.inline_data or not part.inline_data.data:
            return "Failed to generate image: no image data returned."

        image_bytes = part.inline_data.data
        mime_type = part.inline_data.mime_type or "image/jpeg"
        ext = "png" if "png" in mime_type else "jpg"

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"plant_image_{timestamp}.{ext}"

        # 1. Save as ADK Artifact for Playground
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload directly from bytes to public Cloud Storage bucket (no local disk write)
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating or uploading plant image: {str(e)}"


async def generate_plant_video(
    prompt: str,
    tool_context: ToolContext,
) -> str:
    """Generates a short video for a plant or greenhouse item using Google's Omni model (gemini-omni-flash-preview) in the global region.
    Saves the generated video as an ADK artifact and uploads it directly from memory bytes to a public Cloud Storage bucket, returning its public URL.

    Args:
        prompt: Description of the plant or greenhouse video scene to generate (e.g. 'A timelapse of a blooming orchid in a lush greenhouse').

    Returns:
        The public Cloud Storage https URL of the uploaded MP4 video.
    """
    try:
        genai_client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global",
        )

        interaction = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=f"A short video of: {prompt}",
        )

        if not hasattr(interaction, "output_video") or not interaction.output_video:
            return "Failed to generate video: no video output in model response."

        raw_data = interaction.output_video.data
        if isinstance(raw_data, str):
            video_bytes = base64.b64decode(raw_data)
        else:
            video_bytes = bytes(raw_data)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"plant_video_{timestamp}.mp4"

        # 1. Save as ADK Artifact for Playground
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload directly from memory bytes to public Cloud Storage bucket (no local disk write)
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type="video/mp4")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating or uploading plant video: {str(e)}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are the Smart Greenhouse assistant. You help plant enthusiasts manage "
        "their plant catalog, check plant care requirements, log watering dates, "
        "check watering schedule alerts, fetch environmental weather, geocode addresses, "
        "find nearby florists/nurseries, generate images or videos of plants/greenhouse items, "
        "execute Python code safely in a sandbox environment for data analysis, and "
        "remember user preferences, facts, and ALL user allergies across sessions using Memory Bank. "
        "Always record user allergies into long-term memory and check remembered allergies when "
        "recommending plants or care instructions."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=_get_code_executor(),
    instruction=instruction,
    tools=[
        PreloadMemoryTool(),
        list_plants,
        get_plant_details,
        log_plant_care,
        add_plant,
        check_watering_schedule_alerts,
        get_environmental_weather_data,
        geocode_address,
        find_nearby_places,
        generate_plant_image,
        generate_plant_video,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
