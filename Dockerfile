FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    LD_LIBRARY_PATH=/app/bin:${LD_LIBRARY_PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 git curl build-essential unzip cmake rustc cargo patchelf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 仅复制依赖清单和本地 wheel（解决网络下载不稳定）
COPY backend/requirements.txt /app/backend/requirements.txt
COPY assets/models/_incoming/archives/python_crfsuite-0.9.11-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl /app/wheels/

RUN python -m pip install --no-cache-dir -U pip \
    && python -m pip install --no-cache-dir python_crfsuite==0.9.11 \
    && python -m pip install --no-cache-dir --prefer-binary -r /app/backend/requirements.txt \
    && python -m pip install --no-cache-dir torch==2.3.1 torchaudio==2.3.1 \
    && python -m pip install --no-cache-dir whisperx TTS soundfile requests \
    # Fix: some ctranslate2 wheels require executable stack; Docker Desktop may forbid it.
    # Clear execstack flag to allow importing whisperx/ctranslate2 safely.
    && python -c "import glob,subprocess; paths=glob.glob('/usr/local/lib/python3.11/site-packages/**/libctranslate2-*.so*', recursive=True); [subprocess.run(['patchelf','--clear-execstack',p], check=False) for p in paths]; print('[fix] patched libctranslate2 execstack:', len(paths))"

# 准备构建所需的源码与压缩包（确保为 Linux x86_64）
COPY assets/models/_incoming/asr/whisper.cpp-1.8.2.zip /app/
COPY assets/models/_incoming/tts/piper_linux_x86_64.tar.gz /app/scripts/
COPY assets/models/_incoming/archives/ffmpeg-release-amd64-static.tar.xz /app/scripts/

# 构建/解压后端所需可执行文件到 /app/bin
RUN set -eux; \
    mkdir -p /app/bin; \
    unzip /app/whisper.cpp-1.8.2.zip -d /tmp; \
    cd /tmp/whisper.cpp-1.8.2 && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j"$(nproc)"; \
    cp /tmp/whisper.cpp-1.8.2/build/bin/whisper-cli /app/bin/whisper-cli; \
    ln -sf /app/bin/whisper-cli /app/bin/main; \
    cp /tmp/whisper.cpp-1.8.2/build/src/libwhisper.so /app/bin/ || true; \
    ln -sf /app/bin/libwhisper.so /app/bin/libwhisper.so.1 || true; \
    cp /tmp/whisper.cpp-1.8.2/build/ggml/src/libggml*.so /app/bin/ || true; \
    ln -sf /app/bin/libggml.so /app/bin/libggml.so.1 || true; \
    cp /tmp/whisper.cpp-1.8.2/build/ggml/src/libggml*.a /app/bin/ || true; \
    tar -xzf /app/scripts/piper_linux_x86_64.tar.gz -C /app/bin --strip-components=0 || true; \
    tar -xf /app/scripts/ffmpeg-release-amd64-static.tar.xz -C /tmp && cp /tmp/ffmpeg-*-amd64-static/ffmpeg /app/bin/ffmpeg; \
    chmod +x /app/bin/* || true; \
    rm -rf /tmp/whisper.cpp-1.8.2 /tmp/ffmpeg-*-amd64-static

# 复制项目核心代码；模型仍通过挂载提供
COPY backend /app/backend
COPY scripts /app/scripts
COPY config /app/config
COPY replacements.json /app/replacements.json

EXPOSE 5175

CMD ["python", "-m", "backend.app"]

