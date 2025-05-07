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
    s3 = boto3.client('s3')
    mongo_client = MongoClient('mongodb://3.91.45.234:27017/')
    db = mongo_client['hotel_db']

    # Parse SNS message
    sns_message = json.loads(event['Records'][0]['Sns']['Message'])
    hotel_id = sns_message['hotel_id']

    # Get all images for this hotel
    images = list(db.hotel_images.find({"hotel_id": hotel_id}))

    if not images:
        print(f"No images found for hotel {hotel_id}")
        return {
            "statusCode": 404,
            "body": json.dumps({"message": "No images found"})
        }

    # Score each image and update ratings
    best_score = -1
    best_image = None

    for img in images:
        try:
            # Rate the image (0-100)
            rating = rate_image(bedrock, s3, img['image_url'])

            # Update rating in hotel_images table
            db.hotel_images.update_one(
                {"_id": img['_id']},
                {"$set": {
                    "rating": rating
                }}
            )

            # Track best image
            if rating > best_score:
                best_score = rating
                best_image = img

        except Exception as e:
            print(f"Failed to process image {img['image_id']}: {str(e)}")
            continue

    if not best_image:
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "All image ratings failed"})
        }

    # Update hotel with best image
    db.hotels.update_one(
        {"hotel_id": hotel_id},
        {"$set": {
            "main_image_url": best_image['image_url'],
            "main_image_id": best_image['image_id'],
            "main_image_rating": best_score
        }},
        upsert=True
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "best_image_id": best_image['image_id'],
            "rating": best_score,
            "message": "Successfully updated ratings"
        })
    }


def rate_image(bedrock, s3, image_url):
    """Rate image quality using Claude 3 (0-100 scale)"""
    bucket, key = parse_s3_url(image_url)
    image_data = s3.get_object(Bucket=bucket, Key=key)['Body'].read()

    prompt = """Analyze this hotel image and provide a quality score (0-100) considering:
    1. Composition and framing (30%)
    2. Technical quality (25%)
    3. Aesthetic appeal (20%) 
    4. Representative value (25%)

    Return ONLY JSON format:
    {"score": 75, "reason": "Well composed but slightly dark"}"""

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
                            "media_type": get_media_type(image_url),
                            "data": base64.b64encode(image_data).decode('utf-8')
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }],
            "max_tokens": 200
        })
    )

    result = json.loads(response['body'].read())['content'][0]['text']
    return int(json.loads(result)['score'])


def parse_s3_url(url):
    """Extract bucket and key from S3 URL"""
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc.endswith('.s3.amazonaws.com'):
        raise ValueError(f"Invalid S3 URL: {url}")
    return parsed.netloc.split('.')[0], parsed.path.lstrip('/')


def get_media_type(url):
    """Get media type from file extension"""
    ext = '.' + url.split('.')[-1].lower()
    return IMAGE_EXTENSIONS.get(ext, 'image/jpeg')