"""
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import json
import boto3
import base64
import urllib.parse
from pymongo import MongoClient
from bson import ObjectId


def lambda_handler(event, context):
    # Initialize clients
    bedrock = boto3.client('bedrock-runtime')
    s3 = boto3.client('s3')
    mongo_client = MongoClient('mongodb://3.91.45.234:27017/')
    db = mongo_client['hotel_db']

    # Parse SNS message
    sns_message = json.loads(event['Records'][0]['Sns']['Message'])
    hotel_id = sns_message['hotel_id']

    # Get all rooms for this hotel (if any exist)
    rooms = list(db.hotel_rooms.find({"hotel_id": hotel_id})) or [None]
    
    # Get hotel images from DB
    hotel_images = list(db.hotel_images.find({"hotel_id": hotel_id}))
    
    if not hotel_images:
        print(f"No images found for hotel {hotel_id}")
        return {
            "statusCode": 404,
            "body": json.dumps({"message": "No images found"})
        }
    
    # Process each image one by one and collect unique amenities
    all_amenities = set()
    max_amenities = 10
    
    for img in hotel_images:
        # Stop if we've already found 10 unique amenities
        if len(all_amenities) >= max_amenities:
            print(f"Reached {max_amenities} unique amenities, stopping image analysis")
            break
            
        try:
            # Get image data from S3
            bucket, key = parse_s3_url(img['image_url'])
            image_data = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
            encoded_image = base64.b64encode(image_data).decode('utf-8')
            media_type = get_media_type(img['image_url'])
            
            # Process single image with Claude
            image_amenities = get_amenities_from_bedrock(bedrock, encoded_image, media_type)
            
            # Add unique amenities to our set
            all_amenities.update(image_amenities)
            
            # If we now have more than max_amenities, trim the list
            if len(all_amenities) > max_amenities:
                all_amenities = set(list(all_amenities)[:max_amenities])
                
            print(f"Image {img['image_id']} added {len(image_amenities)} amenities, total unique: {len(all_amenities)}")
            
        except Exception as e:
            print(f"Error processing image {img['image_id']}: {str(e)}")
            continue
    
    # Convert set back to list for processing
    amenities = list(all_amenities)
    print(f"Final amenities list: {amenities}")

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


def get_amenities_from_bedrock(bedrock, encoded_image, media_type):
    """Extract amenities using Claude 3 for a single image"""
    prompt = """Analyze this hotel image and return ONLY a comma-separated list of these standardized amenity names 
    that you can visibly identify in the image:

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

    # Create message with single image
    message_content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": encoded_image
            }
        },
        {"type": "text", "text": prompt}
    ]

    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{
                "role": "user",
                "content": message_content
            }],
            "max_tokens": 300
        })
    )

    result = json.loads(response['body'].read())['content'][0]['text']
    return list(set(a.strip().lower() for a in result.split(',') if a.strip()))


def parse_s3_url(url):
    """Extract bucket and key from S3 URL"""
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc.endswith('.s3.amazonaws.com'):
        raise ValueError(f"Invalid S3 URL: {url}")
    return parsed.netloc.split('.')[0], parsed.path.lstrip('/')


def get_media_type(url):
    """Get media type from file extension"""
    IMAGE_EXTENSIONS = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
        '.gif': 'image/gif'
    }
    ext = '.' + url.split('.')[-1].lower()
    return IMAGE_EXTENSIONS.get(ext, 'image/jpeg')