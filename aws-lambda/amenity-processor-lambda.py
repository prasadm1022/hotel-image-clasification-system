import json
import boto3
from pymongo import MongoClient
from bson import ObjectId


def lambda_handler(event, context):
    # Initialize clients
    bedrock = boto3.client('bedrock-runtime')
    mongo_client = MongoClient('mongodb://3.91.45.234:27017/')
    db = mongo_client['hotel_db']

    # Parse SNS message
    sns_message = json.loads(event['Records'][0]['Sns']['Message'])
    hotel_id = sns_message['hotel_id']

    # Get all rooms for this hotel (if any exist)
    rooms = list(db.hotel_rooms.find({"hotel_id": hotel_id})) or [None]

    # Call Claude 3 to extract amenities
    amenities = get_amenities_from_bedrock(bedrock)

    # Process amenities
    for amenity in amenities:
        amenity_lower = amenity.lower()

        # First check if this is a general amenity (not room-specific)
        if is_general_amenity(amenity_lower):
            # Create record without room_id for general amenities
            if not db.hotel_amenities.find_one({
                "hotel_id": hotel_id,
                "amenity_name": amenity_lower,
                "room_id": {"$exists": False}
            }):
                db.hotel_amenities.insert_one({
                    "hotel_id": hotel_id,
                    "amenity_id": str(ObjectId()),
                    "amenity_name": amenity_lower,
                    "room_id": ""  # Explicit empty string
                })
            continue

        # For room-specific amenities
        for room in rooms:
            room_id = room['room_id'] if room else ""
            room_type = room['room_type'] if room else ""

            # Check if this combination exists
            exists = db.hotel_amenities.find_one({
                "hotel_id": hotel_id,
                "room_id": room_id,
                "amenity_name": amenity_lower
            })

            if not exists and should_associate_amenity(amenity_lower, room_type):
                db.hotel_amenities.insert_one({
                    "hotel_id": hotel_id,
                    "room_id": room_id,  # Will be empty string if no room
                    "amenity_id": str(ObjectId()),
                    "amenity_name": amenity_lower
                })

    # Dispatch SNS Topic to Trigger next Lambda
    sns = boto3.client('sns')
    sns.publish(
        TopicArn='arn:aws:sns:us-east-1:699453144934:AmnImageProcessed',
        Message=json.dumps({
            "hotel_id": hotel_id
        })
    )


def is_general_amenity(amenity):
    """Check if amenity applies to the whole hotel (not room-specific)"""
    general_amenities = {
        '24-hour-front-desk',
        'free-parking',
        'swimming-pool',
        'fitness-center',
        'spa-services'
    }
    return amenity in general_amenities


def should_associate_amenity(amenity, room_type):
    """Determine if amenity should be associated with this room type"""
    common_amenities = {
        'free-wi-fi',
        'air-conditioning',
        'towels',
        'complimentary-toiletries'
    }

    premium_amenities = {
        'mini-fridge': {'deluxe-room', 'executive-suite', 'penthouse'},
        'coffee/tea-maker': {'deluxe-room', 'executive-suite', 'penthouse'},
        'flat-screen-tv': {'deluxe-room', 'executive-suite', 'penthouse'},
        'hairdryer': {'deluxe-room', 'executive-suite', 'penthouse'},
        'daily-housekeeping': {'deluxe-room', 'executive-suite', 'penthouse', 'standard-room'}
    }

    if amenity in common_amenities:
        return True
    elif amenity in premium_amenities:
        return room_type in premium_amenities[amenity]
    return False


def get_amenities_from_bedrock(bedrock):
    prompt = """Return ONLY a comma-separated list of these standardized amenity names:

    General Amenities (hotel-wide):
    - 24-hour-front-desk
    - free-parking
    - swimming-pool
    - fitness-center
    - spa-services

    Room Amenities:
    - free-wi-fi
    - air-conditioning
    - flat-screen-tv
    - complimentary-toiletries
    - towels
    - hairdryer
    - mini-fridge
    - coffee/tea-maker
    - daily-housekeeping

    Example response: free-wi-fi,air-conditioning,swimming-pool"""

    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            }],
            "max_tokens": 300
        })
    )

    result = json.loads(response['body'].read())['content'][0]['text']
    return list(set(a.strip().lower() for a in result.split(',') if a.strip()))