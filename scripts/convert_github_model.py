import os
import sys
import re
import urllib.request

# GitHub raw URL for the person detection model
GITHUB_MODEL_URL = "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/EPII_CM55M_APP_S/app/scenario_app/allon_sensor_tflm/person_detect_model_data_vela.cc"

def download_c_file(url):
    """Download .cc file from GitHub"""
    print(f"📡 Downloading C source from GitHub...")
    try:
        with urllib.request.urlopen(url) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"❌ Error downloading C file: {e}")
        sys.exit(1)

def extract_hex_array(c_content):
    """Parse C array and extract hex values"""
    print(f"🔍 Parsing C array to extract hex bytes...")
    
    # Find the array initialization block
    # Pattern: const unsigned char array_name[] = { 0xNN, 0xNN, ... };
    pattern = r'const\s+unsigned\s+char\s+\w+\[\]\s*=\s*\{([^}]+)\}'
    match = re.search(pattern, c_content, re.DOTALL)
    
    if not match:
        print("❌ Could not find byte array in C file")
        sys.exit(1)
    
    array_content = match.group(1)
    
    # Extract all hex values (0xNN format)
    hex_values = re.findall(r'0x([0-9a-fA-F]{2})', array_content)
    
    if not hex_values:
        print("❌ No hex values found in array")
        sys.exit(1)
    
    print(f"   Found {len(hex_values)} bytes")
    return hex_values

def convert_to_tflite(hex_values, output_path):
    """Convert hex array to binary .tflite file"""
    print(f"💾 Writing binary .tflite file...")
    
    try:
        # Convert hex strings to bytes
        binary_data = bytes([int(h, 16) for h in hex_values])
        
        # Write to file
        with open(output_path, 'wb') as f:
            f.write(binary_data)
        
        file_size = len(binary_data)
        print(f"   ✅ Created {output_path} ({file_size} bytes)")
        return file_size
    except Exception as e:
        print(f"❌ Error writing .tflite file: {e}")
        sys.exit(1)

def create_labels(output_path, labels):
    """Create labels.txt file"""
    print(f"📝 Creating labels file...")
    
    try:
        with open(output_path, 'w') as f:
            f.write('\n'.join(labels))
        
        print(f"   ✅ Created {output_path} with {len(labels)} labels")
    except Exception as e:
        print(f"❌ Error creating labels file: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("🚀 Starting GitHub Model Conversion...")
    
    # Ensure output directory exists
    os.makedirs('models', exist_ok=True)
    
    # Download and parse C file
    c_content = download_c_file(GITHUB_MODEL_URL)
    hex_values = extract_hex_array(c_content)
    
    # Convert to .tflite
    tflite_path = 'models/person_detection.tflite'
    convert_to_tflite(hex_values, tflite_path)
    
    # Create labels file (for reference, not used in deployment ZIP)
    labels_path = 'models/labels.txt'
    labels = ['no person', 'person']
    create_labels(labels_path, labels)
    
    print("✨ Conversion complete!")
