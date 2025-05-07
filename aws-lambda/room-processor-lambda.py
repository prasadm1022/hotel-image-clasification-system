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
import urllib.parse
import base64
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime

# Media type mapping
IMAGE_EXTENSIONS = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.gif': 'image/gif'
}


def lambda_handler(event, context):
    # Initialize clients
    bedrock = boto3.client('bedrock-runtime')
    mongo_client = MongoClient('mongodb://3.91.45.234:27017/')
    db = mongo_client['hotel_db']

    # Parse SNS message
    sns_message = json.loads(event['Records'][0]['Sns']['Message'])
    hotel_id = sns_message['hotel_id']
    room_image_ids = sns_message['room_image_ids']

    # Get existing rooms (for deduplication)
    existing_rooms = {
        (doc['room_id'], doc['room_type'].lower())
        for doc in db.hotel_rooms.find({"hotel_id": hotel_id})
    }

    # Process each room image
    new_rooms = []
    for image_id in room_image_ids:
        # Find the image in hotel_images
        image_data = db.hotel_images.find_one({
            "hotel_id": hotel_id,
            "image_id": image_id
        })
        if not image_data:
            continue

        # Dynamic media type detection
        media_type = get_media_type(image_data['image_url'])

        try:
            room_name, room_type = categorize_room(bedrock, image_data['image_url'], media_type)

            # Try to extract room_id from S3 path (format: s3/hotels/rooms/r23/img.png)
            try:
                path_parts = image_data['image_url'].split('/')
                room_id = path_parts[-2]  # Gets "r23" from the path
            except Exception as e:
                print(f"Failed to extract room_id from {image_data['image_url']}: {str(e)}")
                # Use room_type as room_id if directory structure not found
                room_id = room_type.lower().replace('_', '-')  # Convert to URL-friendly format

            # Update hotel_images with room_id
            db.hotel_images.update_one(
                {"_id": image_data['_id']},
                {"$set": {"room_id": room_id}}
            )

            # Check if this room_id + type combination already exists
            if (room_id, room_type.lower()) not in existing_rooms:
                db.hotel_rooms.insert_one({
                    "hotel_id": hotel_id,
                    "image_id": image_id,
                    "room_id": room_id,
                    "room_name": room_name,
                    "room_type": room_type,
                    "created_at": datetime.utcnow()
                })
                existing_rooms.add((room_id, room_type.lower()))
        except Exception as e:
            print(f"Failed to process {image_id}: {str(e)}")
            continue

    # Dispatch SNS Topic to Trigger next Lambda
    sns = boto3.client('sns')
    sns.publish(
        TopicArn='arn:aws:sns:us-east-1:699453144934:RoomImageProcessed',
        Message=json.dumps({
            "hotel_id": hotel_id
        })
    )


def categorize_room(bedrock, image_url, media_type):
    """Extract room name and type using Claude 3"""
    bucket, key = parse_s3_url(image_url)

    # Download image
    s3 = boto3.client('s3')
    image_data = s3.get_object(Bucket=bucket, Key=key)['Body'].read()

    prompt = """Analyze this hotel room image and return JSON with:
    - "name": Creative name (max 3 words)
    - "type": Standardized from: single_room, double_room, twin_room, triple_room, 
               quad_room, studio_room, suite, junior_suite, executive_room, 
               presidential_suite, family_room, connecting_rooms, adjoining_rooms, 
               accessible_room, smoking_room, pet-friendly_room, themed_room"""

    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_data).decode('utf-8')
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }],
            "max_tokens": 300
        })
    )

    response_body = json.loads(response['body'].read())
    claude_response = json.loads(response_body['content'][0]['text'])
    return claude_response['name'], claude_response['type']


def parse_s3_url(url):
    """Extract bucket and key from S3 URL"""
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc.endswith('.s3.amazonaws.com'):
        raise ValueError("Invalid S3 URL format")

    bucket = parsed.netloc.split('.')[0]
    key = parsed.path.lstrip('/')
    return bucket, key


def get_media_type(url):
    """Determine media type from file extension"""
    last_dot = url.rfind('.')
    if last_dot == -1:
        return 'image/jpeg'

    extension = url[last_dot:].lower()
    return IMAGE_EXTENSIONS.get(extension, 'image/jpeg')