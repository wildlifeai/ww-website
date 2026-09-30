# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build tiny TFLite flatbuffers for tests, with the Vela bindings (no TensorFlow).

A model here is a single subgraph with one input and one output tensor, no
operators. That is enough for everything the API side reads: tensor dtype, shape
and quantisation parameters (``domain/model.py::read_io_tensors``, LM-1).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import flatbuffers
from ethosu.vela.tflite import Model, QuantizationParameters, SubGraph, Tensor
from ethosu.vela.tflite.TensorType import TensorType

DTYPES = {"INT8": TensorType.INT8, "FLOAT32": TensorType.FLOAT32, "UINT8": TensorType.UINT8}


def _tensor(builder, name: str, dtype: str, shape: List[int], quant: Optional[Tuple[float, int]]):
    name_off = builder.CreateString(name)
    Tensor.TensorStartShapeVector(builder, len(shape))
    for dim in reversed(shape):
        builder.PrependInt32(dim)
    shape_off = builder.EndVector()
    quant_off = None
    if quant is not None:
        scale, zero_point = quant
        QuantizationParameters.QuantizationParametersStartScaleVector(builder, 1)
        builder.PrependFloat32(scale)
        scale_off = builder.EndVector()
        QuantizationParameters.QuantizationParametersStartZeroPointVector(builder, 1)
        builder.PrependInt64(zero_point)
        zp_off = builder.EndVector()
        QuantizationParameters.QuantizationParametersStart(builder)
        QuantizationParameters.QuantizationParametersAddScale(builder, scale_off)
        QuantizationParameters.QuantizationParametersAddZeroPoint(builder, zp_off)
        quant_off = QuantizationParameters.QuantizationParametersEnd(builder)
    Tensor.TensorStart(builder)
    Tensor.TensorAddName(builder, name_off)
    Tensor.TensorAddShape(builder, shape_off)
    Tensor.TensorAddType(builder, DTYPES[dtype])
    if quant_off is not None:
        Tensor.TensorAddQuantization(builder, quant_off)
    return Tensor.TensorEnd(builder)


def build_classifier_tflite(
    *,
    input_shape: List[int],
    output_classes: int,
    dtype: str = "INT8",
    input_quant: Optional[Tuple[float, int]] = (1.0, -128),
    output_quant: Optional[Tuple[float, int]] = (1.0 / 256, -128),
    scratch_bytes: Optional[int] = None,
) -> bytes:
    """A flatbuffer with one input ``input_shape`` and one output ``[1, output_classes]``.

    ``scratch_bytes`` adds the ``_split_1_scratch`` / ``_scratch_fast`` pair Vela
    writes into a compiled model, both that size, as Vela does in Shared_Sram mode.
    """
    builder = flatbuffers.Builder(1024)
    tensors = [
        _tensor(builder, "image", dtype, list(input_shape), input_quant),
        _tensor(builder, "classes", dtype, [1, output_classes], output_quant),
    ]
    if scratch_bytes is not None:
        tensors += [
            _tensor(builder, "_split_1_scratch", "UINT8", [scratch_bytes], None),
            _tensor(builder, "_split_1_scratch_fast", "UINT8", [scratch_bytes], None),
        ]
    SubGraph.SubGraphStartTensorsVector(builder, len(tensors))
    for off in reversed(tensors):
        builder.PrependUOffsetTRelative(off)
    tensors_off = builder.EndVector()
    SubGraph.SubGraphStartInputsVector(builder, 1)
    builder.PrependInt32(0)
    inputs_off = builder.EndVector()
    SubGraph.SubGraphStartOutputsVector(builder, 1)
    builder.PrependInt32(1)
    outputs_off = builder.EndVector()
    SubGraph.SubGraphStart(builder)
    SubGraph.SubGraphAddTensors(builder, tensors_off)
    SubGraph.SubGraphAddInputs(builder, inputs_off)
    SubGraph.SubGraphAddOutputs(builder, outputs_off)
    subgraph_off = SubGraph.SubGraphEnd(builder)
    Model.ModelStartSubgraphsVector(builder, 1)
    builder.PrependUOffsetTRelative(subgraph_off)
    subgraphs_off = builder.EndVector()
    Model.ModelStart(builder)
    Model.ModelAddVersion(builder, 3)
    Model.ModelAddSubgraphs(builder, subgraphs_off)
    model_off = Model.ModelEnd(builder)
    builder.Finish(model_off, file_identifier=b"TFL3")
    return bytes(builder.Output())


def write_artifact_dir(
    directory: Path,
    *,
    labels: List[str],
    image_size: int = 96,
    channels: int = 1,
    output_classes: Optional[int] = None,
    dtype: str = "INT8",
    input_quant: Optional[Tuple[float, int]] = (1.0, -128),
    accuracy: float = 0.91,
) -> Path:
    """An output directory the way the training container writes it."""
    import json

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    n = output_classes if output_classes is not None else len(labels)
    model = build_classifier_tflite(input_shape=[1, image_size, image_size, channels], output_classes=n, dtype=dtype, input_quant=input_quant)
    (directory / "model_int8.tflite").write_bytes(model)
    (directory / "labels.txt").write_text("\n".join(labels) + "\n", encoding="utf-8", newline="\n")
    (directory / "metrics.json").write_text(json.dumps({"accuracy": accuracy}), encoding="utf-8", newline="\n")
    return directory
