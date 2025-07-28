from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from geopy.geocoders import Nominatim
from datetime import datetime, timedelta, timezone
from geopy.distance import geodesic
import requests
import polyline
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Setup template directory
templates = Jinja2Templates(directory="templates")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
load_dotenv()

# Route to serve frontend
@app.get("/", response_class=HTMLResponse)
async def serve_frontend(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
OPENAQ_API_KEY = os.getenv("OPENAQ_API_KEY")
debugMode = os.getenv("DEBUG")
#print(GOOGLE_MAPS_API_KEY)
#print (OPENAQ_API_KEY)
# Geolocator setup
geolocator = Nominatim(user_agent="AQIMaps")

@app.get("/get_route")
def get_route(origin: str = Query(...), destination: str = Query(...)):
    try:
        sLoc = geolocator.geocode(origin)
        eLoc = geolocator.geocode(destination)
        GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
        OPENAQ_API_KEY = os.getenv("OPENAQ_API_KEY")


        #print(GOOGLE_MAPS_API_KEY)
        #print (OPENAQ_API_KEY)
        if not sLoc or not eLoc:
            return {"error": "Could not geocode origin or destination"}

        sLat, sLong = sLoc.latitude, sLoc.longitude
        eLat, eLong = eLoc.latitude, eLoc.longitude

        start_coords = f"{sLat},{sLong}"
        end_coords = f"{eLat},{eLong}"

        directions_url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            "origin": start_coords,
            "destination": end_coords,
            "mode": "driving",
            "alternatives": "true",
            "key": GOOGLE_MAPS_API_KEY
        }
        response = requests.get(directions_url, params=params)
        data = response.json()
        #print (response)
        #print (data)
        results = []
        polyline_list = []
        best_aqi_value = 10000000
        best_aqi_path = 0

        for i, route in enumerate(data["routes"]):
            if (debugMode) :
                print("Route # ",i)
            polyline_str = route["overview_polyline"]["points"]
            polyline_list.append(polyline_str)
            #polyline_str = data['routes'][0]['overview_polyline']['points']
            coordinates = polyline.decode(polyline_str)

            # Sample every Nth point for waypoints
            num_waypoints = len(coordinates)
            if num_waypoints >20 :
                offset = max(1, int(num_waypoints /10))
            elif num_waypoints >10 :
                offset = max(1,int(num_waypoints/5))
            else :
                offset = 2
            #print (num_waypoints,offset)
            waypoint_coords = [coordinates[i] for i in range(0, num_waypoints, offset)]

            # Add start and end manually
            waypoint_coords.insert(0, (sLat, sLong))
            waypoint_coords.append((eLat, eLong))
            #print ("waypoint coords={}",waypoint_coords)
            path_aqi = 0

            for lat, lon in waypoint_coords:
                pm25 = fetch_pm25_from_openaq(lat, lon)
                aqi = compute_aqi_pm25(pm25) if pm25 is not None else 0
                if aqi ==0 :
                    aqi = fetch_aqi_from_google(lat,lon)
                if (debugMode) :
                    print("lat={} long={} aqi={}",lat,lon,aqi)
                results.append({"lat": lat, "lon": lon, "pm25": pm25, "aqi": aqi})
                path_aqi = path_aqi + aqi
            if path_aqi < best_aqi_value :
                best_aqi_path = i
                best_aqi_value = path_aqi


        #print (results)
        #print (polyline_str)
        if (debugMode) :
            print (results)
        return  {
            "polyline":polyline_list,
            "aqi_points":results,
            "debugMode": debugMode,
            "best_aqi_path_index":best_aqi_path
        }
    
    except Exception as e:
        print("hit an exception",e)
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def fetch_pm25_from_openaq(lat, lon, radius=20000):
    url = "https://api.openaq.org/v3/locations"
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
    params = {
        "coordinates": f"{lat},{lon}",
        "radius": radius,
        "limit": 20,
        "parameter": "pm25",
        "isMonitor": True
    }
    headers = {"X-API-Key": OPENAQ_API_KEY}

    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    #print("response={},data={}",response,data)

    shortest_distance = float("inf")
    best_location = None
    for result in data.get("results", []):
        last_seen_str= None
        last_seen = cutoff_date
        distance = 100000

        if result.get("datetimeLast") is not None:
            last_seen_str = result.get("datetimeLast", {}).get("utc")

        if last_seen_str:
            last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
            if last_seen >= cutoff_date:
                try :
                    distance = result.get("distance", float("inf"))
                except Exception as e:
                    print("Not able to get distance",e)
                    print (result)
                    continue

                if distance < shortest_distance:
                    best_location = result
                    shortest_distance = distance

    if not best_location:
        return None

    # Extract PM2.5 value from latest reading
    location_id = best_location["id"]
    sensors = best_location.get("sensors", [])
    pm25_sensor_ids = [s["id"] for s in sensors if s.get("parameter", {}).get("name") == "pm25"]

    latest_url = f"https://api.openaq.org/v3/locations/{location_id}/latest"
    latest_params = {"parameter": "pm25"}
    latest_response = requests.get(latest_url, params=latest_params, headers=headers)
    latest_data = latest_response.json()

    for r in latest_data.get("results", []):
        if r.get("sensorsId") in pm25_sensor_ids:
            return r.get("value")

    return None

def fetch_aqi_from_google(lat,lon) :
    google_url = f"https://airquality.googleapis.com/v1/currentConditions:lookup?key={GOOGLE_MAPS_API_KEY}"
    payload = {
        "location": {
            "latitude": lat,
            "longitude": lon
        }
    }

    google_response = requests.post(google_url, json=payload).json()
    if "indexes" in google_response:
        g_index = google_response["indexes"][0]
        #print("found aqi {} for lat {} lon {} from google",lat,lon)
        if 'aqi' in g_index :
            return g_index['aqi']
        else:
            return 0
    else :
        #print("even gmaps has no aqi for {} {}",lat,lon)
        #print (google_response)
        return 0

def compute_aqi_pm25(pm25):
    if pm25 is None:
        return None

    breakpoints = [
        (0.0, 12.0, 0, 50),
        (12.1, 35.4, 51, 100),
        (35.5, 55.4, 101, 150),
        (55.5, 150.4, 151, 200),
        (150.5, 250.4, 201, 300),
        (250.5, 500.4, 301, 500),
    ]
    for c_lo, c_hi, i_lo, i_hi in breakpoints:
        if c_lo <= pm25 <= c_hi:
            aqi = ((i_hi - i_lo) / (c_hi - c_lo)) * (pm25 - c_lo) + i_lo
            return round(aqi)
    return None
