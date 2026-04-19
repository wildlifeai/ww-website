# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pre-trained model registry — extracted verbatim from app.py L32-78.

Each entry maps a model name → available resolutions → download URL + type.
"""

MODEL_REGISTRY = {
    "Person Detection": {
        "resolutions": {
            "96x96": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/EPII_CM55M_APP_S/app/scenario_app/allon_sensor_tflm/person_detect_model_data_vela.cc",
                "type": "cc_array",
                "filename": "person_detect_model_data_vela.cc",
            }
        },
        "labels": ["no person", "person"],
    },
    "YOLOv8 Object Detection": {
        "resolutions": {
            "192x192": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolov8_od/yolov8n_od_192_delete_transpose_0xB7B000.tflite",
                "type": "tflite",
                "filename": "yolov8n_od_192.tflite",
            }
        },
        "labels": ["object"],
    },
    "YOLOv11 Object Detection": {
        "resolutions": {
            "192x192": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolo11_od/yolo11n_full_integer_quant_192_241219_batch_matmul_vela.tflite",
                "type": "tflite",
                "filename": "yolo11n_od_192.tflite",
            },
            "224x224": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolo11_od/yolo11n_full_integer_quant_vela_imgz_224_kris_nopost_241230.tflite",
                "type": "tflite",
                "filename": "yolo11n_od_224.tflite",
            },
        },
        "labels": ["object"],
    },
    "YOLOv8 Pose Estimation": {
        "resolutions": {
            "256x256": {
                "url": "https://raw.githubusercontent.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/main/model_zoo/tflm_yolov8_pose/yolov8n_pose_256_vela_3_9_0x3BB000.tflite",
                "type": "tflite",
                "filename": "yolov8n_pose_256.tflite",
            }
        },
        "labels": ["person_pose"],
    },
}


def get_model_config(model_type: str, resolution: str) -> dict:
    """Safe retrieval of a model's download config.

    Raises:
        ValueError: If model_type or resolution is unknown.
    """
    try:
        return MODEL_REGISTRY[model_type]["resolutions"][resolution]
    except KeyError:
        raise ValueError(f"Configuration not found for {model_type} at {resolution}")
