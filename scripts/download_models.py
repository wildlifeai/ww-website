import os
import urllib.request
import http.cookiejar

def download_file_from_google_drive(file_id, destination):
    url = "https://docs.google.com/uc?export=download&id=" + file_id
    
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    
    try:
        # Initial request to check for virus scan warning
        response = opener.open(url)
        
        # Check for confirmation token in cookies
        confirm_token = None
        for cookie in cj:
            if cookie.name.startswith('download_warning'):
                confirm_token = cookie.value
                break
        
        # If warning found, perform a second request with the confirm token
        if confirm_token:
            confirm_url = url + "&confirm=" + confirm_token
            response = opener.open(confirm_url)
            
        # Download in chunks to be memory efficient
        with open(destination, 'wb') as f:
            while True:
                chunk = response.read(1024 * 1024) # 1MB chunks
                if not chunk:
                    break
                f.write(chunk)
        
        file_size = os.path.getsize(destination)
        print(f"✅ Successfully downloaded {destination} ({file_size} bytes)")
        
    except (urllib.error.URLError, http.client.HTTPException) as e:
        print(f"❌ Error downloading {destination}: {str(e)}")

if __name__ == "__main__":
    # Ensure models directory exists
    if not os.path.exists('models'):
        os.makedirs('models')
        print("Created 'models' directory.")
        
    models = {
        '1r6COitGZbkkvnIIQSDbHH9ga_968Prfx': 'models/person_detection.tflite',
        '1SXMds8ho22pSIIVzm1_JLno-BkxFfA0E': 'models/rat_detection.tflite'
    }
    
    print("🚀 Starting AI Model Downloads...")
    for file_id, dest in models.items():
        print(f"📡 Requesting {dest}...")
        download_file_from_google_drive(file_id, dest)
    print("✨ Finished.")
