name: Run Current Affairs Script

on:
  push:
    branches: [ main ]
  workflow_dispatch:

jobs:
  run-script:
    runs-on: ubuntu-latest
    
    steps:
    - name: Checkout code
      uses: actions/checkout@v2

    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v1

    - name: Build and push Docker image
      uses: docker/build-push-action@v2
      with:
        context: .
        push: false
        load: true
        tags: current-affairs:latest

    - name: Run Docker container
      env:
        DB_NAME: ${{ secrets.DB_NAME }}
        COLLECTION_NAME: ${{ secrets.COLLECTION_NAME }}
        MONGO_CONNECTION_STRING: ${{ secrets.MONGO_CONNECTION_STRING }}
        TEMPLATE_URL: ${{ secrets.TEMPLATE_URL }}
        TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        TELEGRAM_CHANNEL_ID: ${{ secrets.TELEGRAM_CHANNEL_ID }}
      run: |
        docker run --rm \
          -e DB_NAME \
          -e COLLECTION_NAME \
          -e MONGO_CONNECTION_STRING \
          -e TEMPLATE_URL \
          -e TELEGRAM_BOT_TOKEN \
          -e TELEGRAM_CHANNEL_ID \
          current-affairs:latest
