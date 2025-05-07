# Hotel Image Classification System

Team Insight Guild ~ [Gen AI Buildathon 2025 @ CodeGen International Pvt Ltd]

https://fb.watch/zrhMduZ318/

https://www.facebook.com/lifeatcodegen.int/posts/pfbid02DayKXaKiUhofFFNjRRbrPe8TLJE1XhEKm7fV5DiJacbP2aYqB7yFNTtaem6DBU4bl

A system that has been based on AWS that uses AI to analyze and categorize hotel related images, extract specific
information.

### Features

- Categorize hotel images into categories like exterior, interior, foods, rooms etc.
- Categorize identified room images into common room types like suite, deluxe, standard etc.
- Extract hotel wise facilities
- Extract room wise facilities
- Calculate ratings for images based on quality, clarity, and relevance and select cover image for the hotel.
- Calculate ratings for images based on quality, clarity, and relevance and select cover image for the room.

### Components

- **Python based web application:** For uploading images & showing results
- **Amazon S3:** For storing images
- **AWS Lambda:** For serverless processing of images
- **AWS Bedrock:** For AI analysis using "Claude 3 Sonnet"
- **Amazon SNS:** For triggering Lambda functions based on events
- **EC2 & MongoDB:** For storing and managing data

## Data Flow Diagram

```mermaid
graph TD
    U1(user) -->|upload images| S3[<b>Amazon S3</b>]
    S3 -->|triggers| L1[<b>hotel-processor-lambda</b>]
    ABR[<b>Amazon Bedrock</b><br/>Claude 3 Sonnet] -.->|<b>AI Analysis</b><br>hotel image categories| L1
    L1 -.->|store<br>hotel categories| DB1[(<b>MongoDB</b><br/>hotel data)]
    L1 -->|lambda<br>completed| SNS1[<b>Amazon SNS</b><br>HotelImageProcessed]
    SNS1 -->|triggers| L2[<b>room-processor-lambda</b>]
    ABR -.->|<b>AI Analysis</b><br>room image categories| L2
    L2 -.->|store<br>room categories| DB2[(<b>MongoDB</b><br/>room data)]
    L2 -->|lambda<br>completed| SNS2[<b>Amazon SNS</b><br>RoomImageProcessed]
    SNS2 -->|triggers| L3[<b>amenity-processor-lambda</b>]
    ABR -.->|<b>AI Analysis</b><br>hotel amenities| L3
    L3 -.->|store<br>hotel facilities| DB1
    L3 -.->|store<br>room facilities| DB2
    L3 -->|lambda<br>completed| SNS3[<b>Amazon SNS</b><br>AmnImageProcessed]
    SNS3 -->|triggers| L4[<b>rating-calculator-lambda</b>]
    ABR -.->|<b>AI Analysis</b><br>image ratings| L4
    L4 -.->|store<br>hotel image ratings| DB1
    L4 -.->|store<br>room image ratings| DB2
    DB1 -.->|<b>retrieve data</b>| API[<b>API</b>]
    DB2 -.->|<b>retrieve data</b>| API
    U2(users/frontend) -->|request hotel/room data| API
```