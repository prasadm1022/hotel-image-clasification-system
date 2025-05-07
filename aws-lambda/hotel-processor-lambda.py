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

import boto3
import json
import base64
from pymongo import MongoClient
from bson import ObjectId


def lambda_handler(event, context):
    s3 = boto3.client('s3')
    bedrock = boto3.client('bedrock-runtime')
    mongo_client = MongoClient('mongodb://3.91.45.234:27017/')
    db = mongo_client['hotel_db']

    processed_hotel_ids = set()  # Track hotels we've processed in this invocation

    hotel_id = ''

    for record in event['Records']:
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']

        # Extract hotel_id from folder name
        path_parts = key.split('/')
        if len(path_parts) < 3:
            print(f"Invalid key format: {key}. Expected format: 'hotels/<hotel_id>/<image>'")
            continue

        hotel_id = path_parts[1]
        image_id = path_parts[-1]
        image_url = f"https://{bucket}.s3.amazonaws.com/{key}"

        # Check if this image already exists in the database
        if db.hotel_images.find_one({"image_id": image_id, "hotel_id": hotel_id}):
            print(f"Duplicate image detected - skipping: {image_id}")
            continue

        # Categorize image with Claude 3
        category = categorize_image(bedrock, bucket, key)

        # Check for existing category entry
        existing_context = db.hotel_context.find_one({
            "hotel_id": hotel_id,
            "image_id": image_id,
            "category": category
        })

        if not existing_context:
            # Insert records only if they don't exist
            db.hotel_images.insert_one({
                "hotel_id": hotel_id,
                "image_id": image_id,
                "image_url": image_url
            })
            db.hotel_context.insert_one({
                "hotel_id": hotel_id,
                "image_id": image_id,
                "category": category
            })

        # Track processed hotels for SNS notification
        processed_hotel_ids.add(hotel_id)

    # Dispatch SNS notifications (one per hotel)
    sns = boto3.client('sns')
    for hotel_id in processed_hotel_ids:
        # Get only new room images that were just processed
        new_room_images = [
            img["image_id"] for img in db.hotel_context.find({
                "hotel_id": hotel_id,
                "category": "rooms"
            })
        ]

        if new_room_images:
            sns.publish(
                TopicArn='arn:aws:sns:us-east-1:699453144934:HotelImageProcessed',
                Message=json.dumps({
                    "hotel_id": hotel_id,
                    "room_image_ids": new_room_images
                })
            )


def categorize_image(bedrock, bucket, key):
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket, Key=key)
    image_data = base64.b64encode(response['Body'].read()).decode('utf-8')

    prompt = """
    Categorize image strictly into ONE of these categories:
    - exterior
    - interior
    - foods
    - leisure
    - parking
    - rooms
    - bathrooms

    Return ONLY the category name (no quotes or explanations).
    """

    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                    {"type": "text", "text": prompt}
                ]
            }],
            "max_tokens": 100
        })
    )

    return json.loads(response['body'].read())['content'][0]['text'].strip()