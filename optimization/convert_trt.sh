#!/bin/bash
# YuNet ONNX -> TensorRT 엔진 변환 (Jetson Orin Nano, JetPack 6 / TensorRT 10.3)
# 엔진은 만든 장비·TensorRT 버전에서만 동작하므로, 다른 Jetson에서는 이 스크립트를 다시 실행해야 합니다.
set -e
cd "$(dirname "$0")"
TRTEXEC=/usr/src/tensorrt/bin/trtexec
ONNX=../face_detection_yunet_2023mar.onnx

$TRTEXEC --onnx=$ONNX --saveEngine=yunet_fp32.engine > build_fp32.log 2>&1
$TRTEXEC --onnx=$ONNX --fp16 --saveEngine=yunet_fp16.engine > build_fp16.log 2>&1

grep -E "Engine built in|Throughput|Latency: min|PASSED|FAILED" build_fp32.log build_fp16.log
ls -la yunet_*.engine
