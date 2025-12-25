import os
import sys
import urllib.request
import http.cookiejar
import hashlib

CHUNK_SIZE = 1024 * 1024  # 1MB chunks

def verify_checksum(file_path, expected_checksum):
    """Verify SHA256 checksum of a file."""
    h = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest().lower() == expected_checksum.lower()
    except Exception as e:
        print(f"❌ Error during checksum verification: {e}")
        return False

def download_file_from_google_drive(file_id, destination, expected_checksum=None):
    url = f"https://docs.google.com/uc?export=download&id={file_id}"
    
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    
    try:
        # Initial request to check for virus scan warning
        response = opener.open(url)
        try:
            # Check for confirmation token in cookies
            confirm_token = None
            for cookie in cj:
                if cookie.name.startswith('download_warning'):
                    confirm_token = cookie.value
                    break
            
            # If warning found, perform a second request with the confirm token
            if confirm_token:
                response.close()
                confirm_url = f"{url}&confirm={confirm_token}"
                response = opener.open(confirm_url)
                
            # Download in chunks to be memory efficient
            with open(destination, 'wb') as f:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
        finally:
            response.close()
        
        if expected_checksum:
            if verify_checksum(destination, expected_checksum):
                print(f"✅ Successfully downloaded and verified {destination}")
            else:
                print(f"❌ Checksum verification failed for {destination}")
                os.remove(destination)
        else:
            file_size = os.path.getsize(destination)
            print(f"✅ Successfully downloaded {destination} ({file_size} bytes)")
        
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"❌ Network/HTTP Error downloading {destination}: {e}")
    except Exception as e:
        print(f"❌ Unexpected Error downloading {destination}: {str(e)}")

if __name__ == "__main__":
    # Ensure models directory exists
    os.makedirs('models', exist_ok=True)
        
    models = {
        '1r6COitGZbkkvnIIQSDbHH9ga_968Prfx': {
            'path': 'models/person_detection.tflite',
            'hash': '1c5b023e9c559dd1cca0520c46c0080f7d8b3d2dec76cf5ca466f47dbabb56c0'
        },
        '1SXMds8ho22pSIIVzm1_JLno-BkxFfA0E': {
            'path': 'models/rat_detection.tflite',
            'hash': 'e42d29e9aa8d21952cc8925def5b622aa0940d42d4b56723b8c34519184b4c01'
        }
    }
    
    print("🚀 Starting AI Model Downloads...")
    all_success = True
    for file_id, info in models.items():
        dest = info['path']
        expected_hash = info['hash']
        print(f"📡 Requesting {dest}...")
        try:
            download_file_from_google_drive(file_id, dest, expected_hash)
            if not os.path.exists(dest):
                all_success = False
        except Exception as e:
            print(f"❌ Error downloading {dest}: {e}")
            all_success = False
            
    if not all_success:
        print("❌ One or more downloads failed.")
        sys.exit(1)
        
    print("✨ Finished.")
