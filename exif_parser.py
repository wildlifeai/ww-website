import struct
import io
import re

EXIF_TAGS = {
    0x0132: "DateTime",
    0x9003: "Datetime_Original",
    0x9004: "Datetime_Create",
    0x9286: "UserComment",
    0xC000: "Custom_Data",
    0xF200: "Deployment_ID",
    0x0001: "GPS_Latitude_Reference",
    0x0002: "GPS_Latitude",
    0x0003: "GPS_Longitude_Reference",
    0x0004: "GPS_Longitude",
    0x0005: "GPS_Altitude_Reference",
    0x0006: "GPS_Altitude",
    0x8769: "ExifIFDPointer",
    0x8825: "GPSInfoIFDPointer",
}

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

def format_value(value, type_id):
    if isinstance(value, bytes):
        if type_id == 2:  # ASCII
            try:
                return value.decode('ascii', errors='replace').strip('\x00')
            except Exception:
                return None
        elif type_id in (5, 10):  # RATIONAL or SRATIONAL
            pairs = []
            for i in range(0, len(value), 8):
                if i + 8 > len(value):
                    break
                num, denom = struct.unpack('<II' if type_id == 5 else '<ii', value[i:i+8])
                if denom != 0:
                    pairs.append(num / denom)
                else:
                    pairs.append(0.0)
            return pairs[0] if len(pairs) == 1 else pairs
        elif type_id in (1, 7):  # BYTE or UNDEFINED
            # If it's undefined, try decoding as ASCII just in case it's a string disguised (common for Custom Data)
            try:
                decoded = value.decode('ascii', errors='ignore').strip('\x00')
                if any(c.isalnum() for c in decoded):
                    return decoded
            except:
                pass
            return value.hex()
    return value
    
def parse_ifd(fp, base_offset, ifd_offset, endian, parsed_data, check_next_ifd=True):
    try:
        fp.seek(base_offset + ifd_offset)
        raw = fp.read(2)
        if len(raw) < 2: return
        num_entries = struct.unpack(endian + 'H', raw)[0]
    except Exception:
        return

    for _ in range(num_entries):
        entry_offset = fp.tell()
        entry = fp.read(12)
        if len(entry) < 12: return

        tag, type_id, count, value_offset = struct.unpack(endian + 'HHII', entry)
        type_size = TYPE_SIZES.get(type_id, 1)
        total_size = type_size * count

        tag_name = EXIF_TAGS.get(tag, None)

        # Handle inline values
        if total_size <= 4:
            raw_bytes = struct.pack(endian + 'I', value_offset)
            value = raw_bytes[:total_size]
        else:
            current_pos = fp.tell()
            try:
                fp.seek(base_offset + value_offset)
                value = fp.read(total_size)
            except Exception:
                value = b''
            fp.seek(current_pos)

        if tag_name:
            fmt_val = format_value(value, type_id)
            parsed_data[tag_name] = fmt_val

        # Auto-follow pointer tags
        if tag == 0x8825:  # GPSInfoIFDPointer
            parse_ifd(fp, base_offset, value_offset, endian, parsed_data, check_next_ifd=False)
        elif tag == 0x8769:  # ExifIFDPointer
            parse_ifd(fp, base_offset, value_offset, endian, parsed_data, check_next_ifd=False)

    # Parse next IFD in chain (if any)
    if check_next_ifd:
        next_ifd = fp.read(4)
        if len(next_ifd) == 4:
            next_ifd_offset = struct.unpack(endian + 'I', next_ifd)[0]
            if next_ifd_offset != 0:
                parse_ifd(fp, base_offset, next_ifd_offset, endian, parsed_data)

def extract_exif_from_bytes(file_bytes):
    parsed_data = {}
    fp = io.BytesIO(file_bytes)
    while True:
        marker_start = fp.read(1)
        if not marker_start: break
        if marker_start != b'\xFF': continue
        marker = fp.read(1)
        if marker in [b'\xD8', b'\xD9']: continue
        length_bytes = fp.read(2)
        if len(length_bytes) < 2: break
        length = struct.unpack('>H', length_bytes)[0]
        segment_offset = fp.tell()
        segment_data = fp.read(length - 2)
        
        if marker == b'\xE1':  # APP1 (EXIF)
            if not segment_data.startswith(b'Exif\x00\x00'): continue
            endian_flag = segment_data[6:8]
            if endian_flag == b'II': endian = '<'
            elif endian_flag == b'MM': endian = '>'
            else: continue
            
            if len(segment_data) < 14: continue
            tiff_header_offset = segment_offset + 6
            first_ifd_offset = struct.unpack(endian + 'I', segment_data[10:14])[0]
            
            parse_ifd(fp, tiff_header_offset, first_ifd_offset, endian, parsed_data)
            break
            
    # Process coordinates if they exist
    if 'GPS_Latitude' in parsed_data and 'GPS_Longitude' in parsed_data:
        try:
            lat = parsed_data['GPS_Latitude']
            lon = parsed_data['GPS_Longitude']
            lat_ref = parsed_data.get('GPS_Latitude_Reference', 'N')
            lon_ref = parsed_data.get('GPS_Longitude_Reference', 'E')
            
            lat_deg = lat[0] + lat[1]/60.0 + lat[2]/3600.0
            lon_deg = lon[0] + lon[1]/60.0 + lon[2]/3600.0
            
            if lat_ref == 'S': lat_deg = -lat_deg
            if lon_ref == 'W': lon_deg = -lon_deg
            
            parsed_data['latitude'] = round(lat_deg, 6)
            parsed_data['longitude'] = round(lon_deg, 6)
        except Exception:
            pass
            
    # Normalize Date
    for dt_key in ['DateTime', 'Datetime_Original', 'Datetime_Create']:
         if dt_key in parsed_data:
             parsed_data['date'] = parsed_data[dt_key]
             break
             
    # Try to extract deployment ID
    deployment_id = None
    if 'Deployment_ID' in parsed_data and parsed_data['Deployment_ID']:
         cleaned = str(parsed_data['Deployment_ID']).strip()
         deployment_id = cleaned
    elif 'UserComment' in parsed_data and parsed_data['UserComment']:
         # User comment might start with ASCII or UNDEFINED chars, we cleaned it up in format_value
         cleaned = str(parsed_data['UserComment']).strip()
         if "uuid" in cleaned.lower() or len(cleaned) == 36:
              deployment_id = cleaned[-36:] # just assume it's at the end or it IS the uuid
         else:
              deployment_id = cleaned
    elif 'Custom_Data' in parsed_data and parsed_data['Custom_Data']:
         cleaned = str(parsed_data['Custom_Data']).strip()
         deployment_id = cleaned
         
    # Clean deployment_id (uuid format check fallback)
    if deployment_id:
        match = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', str(deployment_id).lower())
        if match:
             deployment_id = match.group(0)
        else:
             deployment_id = None
             
    parsed_data['deployment_id'] = deployment_id
    
    return parsed_data
