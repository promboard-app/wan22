"""promboard.wan22 — Wan 2.2 영상 만들기 노드 코드.

노드 하나(wan22)가 프롬프트(필수)와 시작 프레임(선택)을 받아 mp4(H.264, 소리 없음) 한 편을 낸다.
시작 프레임을 이으면 이미지→영상, 안 이으면 글→영상이다(5B 는 한 모델이 둘 다 한다).

모델(설정 model, manifest requires.models 의 when 으로 고른 것만 받는다)
  5b-gguf   Wan2.2-TI2V-5B 변환기를 GGUF Q8_0(QuantStack, 5.4GB)으로. 글 인코더(UMT5-XXL) · VAE 는 공식 Diffusers 저장소.
            12GB 그래픽카드용 기본값.
  5b-full   공식 Wan2.2-TI2V-5B-Diffusers 변환기(fp32 20GB 를 bf16 으로 읽는다). 그래픽카드가 20GB 보다 작으면 무게를
            fp8 로 들고 있다가 계산할 때만 bf16 으로 푼다(diffusers layerwise casting).
  t2v-a14b  Wan2.2-T2V-A14B(전문가 둘, 단계마다 14B) GGUF Q8_0. 글→영상만.   (미검증)
  i2v-a14b  Wan2.2-I2V-A14B GGUF Q8_0. 이미지→영상만.                        (미검증)

12GB 에서 돌리는 길(5B)
  - 글 인코더(11.4GB bf16)는 그래픽카드에 통째로 못 올려 블록 묶음 단위로 올렸다 내리며 계산한다(diffusers group offloading).
    프롬프트를 임베딩으로 바꾼 뒤에는 파이프라인에 넣지 않는다(파이프라인은 prompt_embeds 를 받는다).
  - 변환기는 파이프라인 단위 내리기(enable_model_cpu_offload) — 쓸 때만 그래픽카드에 올린다.
  - VAE 는 그래픽카드에 계속 두고(16GB 보다 작으면 bf16) 타일로 나눠 푼다(enable_tiling, 24GB 이상이면 나누지 않는다).
공식 값(Wan2.2 README · diffusers 예시): 5B 720P = 1280×704 · 704×1280, 24fps, 121프레임(5초), 50단계, guidance 5.0.
A14B: 720P 1280×720 · 480P 832×480, 16fps, 81프레임(5초), 40단계, guidance T2V 4.0/3.0 · I2V 3.5/3.5(전문가 high/low).
이미지가 있으면 화면 비는 이미지를 따르고 넓이(가로×세로)는 고른 해상도와 같게 맞춘다(diffusers Wan I2V 예시와 같은 식).

노드 코드 약속(NODES · ctx · 입출력 모양)은 PromBoard docs/Scrum4/02_스키마-v0.md §6.
"""
import gc
import math
import os
import random
import time

DIFFUSERS_5B = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
GGUF_5B = "QuantStack/Wan2.2-TI2V-5B-GGUF/Wan2.2-TI2V-5B-Q8_0.gguf"
DIFFUSERS_T2V = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
DIFFUSERS_I2V = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"
GGUF_T2V = (
    "QuantStack/Wan2.2-T2V-A14B-GGUF/HighNoise/Wan2.2-T2V-A14B-HighNoise-Q8_0.gguf",
    "QuantStack/Wan2.2-T2V-A14B-GGUF/LowNoise/Wan2.2-T2V-A14B-LowNoise-Q8_0.gguf",
)
GGUF_I2V = (
    "QuantStack/Wan2.2-I2V-A14B-GGUF/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q8_0.gguf",
    "QuantStack/Wan2.2-I2V-A14B-GGUF/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q8_0.gguf",
)

# 판마다 공식 값. sizes = 해상도 → (가로, 세로)(가로 영상 기준, 세로 영상은 뒤집는다).
VARIANTS = {
    "5b-gguf": {"family": "5b", "fps": 24, "steps": 50, "guidance": (5.0, None), "sizes": {"720p": (1280, 704), "480p": (832, 480)}},
    "5b-full": {"family": "5b", "fps": 24, "steps": 50, "guidance": (5.0, None), "sizes": {"720p": (1280, 704), "480p": (832, 480)}},
    "t2v-a14b": {"family": "t2v", "fps": 16, "steps": 40, "guidance": (4.0, 3.0), "sizes": {"720p": (1280, 720), "480p": (832, 480)}},
    "i2v-a14b": {"family": "i2v", "fps": 16, "steps": 40, "guidance": (3.5, 3.5), "sizes": {"720p": (1280, 720), "480p": (832, 480)}},
}

# Wan2.2 공식 기본 부정 프롬프트(wan/configs/shared_config.py sample_neg_prompt · diffusers 예시와 같다).
DEFAULT_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
    "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)
MAX_SEQUENCE_LENGTH = 512  # Wan 글 인코더 길이(diffusers WanPipeline 기본)
FULL_GPU_MB = 20_000  # 이보다 작은 그래픽카드면 원본 5B 를 fp8 로 들고 있는다
NO_TILING_MB = 24_000  # 이 이상이면 VAE 를 타일 없이 푼다
VAE_FP32_MB = 16_000  # 이 이상이면 VAE 를 공식 예시대로 fp32 로, 아래면 bf16 으로 그래픽카드에 둔다
TE_ON_GPU_MB = 16_000  # 이 이상이면 글 인코더를 통째로 올린다(아니면 블록 묶음 단위)
# PromBoard 실행기는 노드 한 번에 900초 + 처음 모델 적재 몫 180초를 준다(server/src/plugins/runner/run.ts
# PLUGIN_NODE_BUDGET_SEC · localai/timeouts.ts). 앞 단계 속도로 전체 시간을 어림해 넘을 것 같으면 바로 멈추고 알린다.
TIME_BUDGET_SEC = 1050
DECODE_SHARE = 0.12  # 어림할 때 VAE 풀기 몫(5B 720P 실측 비율에 여유를 둔 값)

_CACHE = {"variant": None, "parts": None, "key": None, "pipe": None, "te": None, "tok": None}


class Progress:
    """ctx.progress 를 [lo, hi] 구간에 맞춰 부른다(뒤로 가지 않는다)."""

    def __init__(self, ctx, lo=0.0, hi=1.0):
        self.ctx, self.lo, self.hi, self.last = ctx, lo, hi, lo

    def __call__(self, fraction, stage="work"):
        value = max(self.last, self.lo + (self.hi - self.lo) * max(0.0, min(1.0, fraction)))
        self.last = value
        self.ctx.progress(value, stage)


def _repo_dir(ctx, repo, probe):
    """받은 저장소 폴더(실행 준비가 <모델 폴더>/_plugin-models/<저장소>@<커밋>/ 에 받는다). probe = 그 안의 받은 파일 하나."""
    path = os.path.normpath(ctx.model_path(f"{repo}/{probe}"))
    for _ in probe.split("/"):
        path = os.path.dirname(path)
    return path


def _vram_mb():
    import torch

    if not torch.cuda.is_available():
        return 0
    return torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)


def _free_cuda():
    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _drop_pipe():
    _CACHE.update(variant=None, parts=None, key=None, pipe=None)
    _free_cuda()


def _oom_message(variant):
    tip = "해상도를 480P 로 낮추거나 길이를 줄여 주세요."
    if variant == "5b-full":
        tip = "모델을 '5B · 가벼운 판'으로 바꾸거나 " + tip
    return f"그래픽카드 메모리가 모자라 멈췄습니다. {tip} 다른 프로그램(게임 · 다른 AI)이 그래픽카드를 쓰고 있으면 닫아 주세요."


# ---------------------------------------------------------------------------
# 글 인코더
# ---------------------------------------------------------------------------


def _text_encoder(ctx, progress):
    """UMT5-XXL 글 인코더 · 토크나이저(5B 저장소 것을 14B 도 같이 쓴다 — 파일 sha256 이 같다)."""
    import torch
    from transformers import AutoTokenizer, UMT5EncoderModel

    root = _repo_dir(ctx, DIFFUSERS_5B, "text_encoder/config.json")
    if _CACHE["te"] is not None:
        return _CACHE["tok"], _CACHE["te"]
    progress(0.0, "load-model")
    start = time.monotonic()
    tok = AutoTokenizer.from_pretrained(os.path.join(root, "tokenizer"))
    te = UMT5EncoderModel.from_pretrained(os.path.join(root, "text_encoder"), torch_dtype=torch.bfloat16)
    te.eval()
    if ctx.device == "cuda":
        if _vram_mb() >= TE_ON_GPU_MB:
            te.to("cuda")
        else:
            from diffusers.hooks import apply_group_offloading

            apply_group_offloading(
                te,
                onload_device=torch.device("cuda"),
                offload_device=torch.device("cpu"),
                offload_type="block_level",
                num_blocks_per_group=4,
            )
    _CACHE["tok"], _CACHE["te"] = tok, te
    ctx.log(f"text encoder loaded in {time.monotonic() - start:.1f}s")
    return tok, te


def _encode(ctx, progress, texts):
    """글 목록 → [임베딩 (1, 512, 4096)] (diffusers WanPipeline._get_t5_prompt_embeds 와 같은 방식: 실제 길이 뒤는 0)."""
    import torch

    tok, te = _text_encoder(ctx, progress)
    progress(0.0, "encode")
    out = []
    for text in texts:
        ids = tok(
            [text],
            padding="max_length",
            max_length=MAX_SEQUENCE_LENGTH,
            truncation=True,
            add_special_tokens=True,
            return_attention_mask=True,
            return_tensors="pt",
        )
        dev = "cuda" if ctx.device == "cuda" else "cpu"
        mask = ids.attention_mask
        seq_len = int(mask.gt(0).sum(dim=1)[0])
        with torch.no_grad():
            emb = te(ids.input_ids.to(dev), mask.to(dev)).last_hidden_state
        emb = emb.to(torch.bfloat16)[0, :seq_len]
        padded = torch.cat([emb, emb.new_zeros(MAX_SEQUENCE_LENGTH - seq_len, emb.size(1))])
        out.append(padded.unsqueeze(0))
        ctx.check_cancel()
    return out


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------


def _gguf_transformer(path, config_root, subfolder):
    import torch
    from diffusers import GGUFQuantizationConfig, WanTransformer3DModel

    return WanTransformer3DModel.from_single_file(
        path,
        quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
        config=config_root,
        subfolder=subfolder,
        torch_dtype=torch.bfloat16,
        local_files_only=True,
    )


def _parts(ctx, progress, variant):
    """판 → 부품(변환기 · VAE · 스케줄러 …). 판이 바뀔 때만 다시 읽는다."""
    import torch
    from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanTransformer3DModel

    if _CACHE["variant"] == variant:
        return _CACHE["parts"]
    _drop_pipe()
    progress(0.0, "load-model")
    start = time.monotonic()
    vram = _vram_mb()
    family = VARIANTS[variant]["family"]
    if family == "5b":
        root = _repo_dir(ctx, DIFFUSERS_5B, "model_index.json")
        if variant == "5b-gguf":
            transformer = _gguf_transformer(ctx.model_path(GGUF_5B), root, "transformer")
        else:
            transformer = WanTransformer3DModel.from_pretrained(os.path.join(root, "transformer"), torch_dtype=torch.bfloat16)
            if ctx.device == "cuda" and vram < FULL_GPU_MB:
                transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16)
        transformer_2 = None
        boundary, expand = None, True
    else:
        repo, files = (DIFFUSERS_T2V, GGUF_T2V) if family == "t2v" else (DIFFUSERS_I2V, GGUF_I2V)
        root = _repo_dir(ctx, repo, "model_index.json")
        transformer = _gguf_transformer(ctx.model_path(files[0]), root, "transformer")
        ctx.check_cancel()
        transformer_2 = _gguf_transformer(ctx.model_path(files[1]), root, "transformer_2")
        boundary = 0.875 if family == "t2v" else 0.9  # 공식 model_index.json boundary_ratio
        expand = False
    ctx.check_cancel()
    # VAE 는 _pipeline 이 그래픽카드에 계속 올려 둔다. 공식 예시는 fp32 — 16GB 보다 작은 그래픽카드는 bf16(절반 크기)으로 둔다.
    vae_dtype = torch.float32 if vram >= VAE_FP32_MB else torch.bfloat16
    vae = AutoencoderKLWan.from_pretrained(os.path.join(root, "vae"), torch_dtype=vae_dtype)
    if vram < NO_TILING_MB:
        vae.enable_tiling()
    parts = dict(
        vae=vae,
        scheduler=UniPCMultistepScheduler.from_pretrained(os.path.join(root, "scheduler")),
        transformer=transformer,
        transformer_2=transformer_2,
        boundary_ratio=boundary,
        expand_timesteps=expand,
    )
    _CACHE.update(variant=variant, parts=parts)
    ctx.log(f"pipeline {variant} loaded in {time.monotonic() - start:.1f}s (vram {vram} MB)")
    return parts


def _pipeline(ctx, progress, variant, i2v=False):
    """글→영상 또는 이미지→영상 파이프라인 하나(부품은 같이 쓴다). 글 인코더는 넣지 않는다(prompt_embeds 로 받는다).

    변환기는 파이프라인 단위 내리기(enable_model_cpu_offload)로 쓸 때만 올린다. VAE 는 그 고리에서 빼고 그래픽카드에 계속 둔다 —
    고리에 두면 VAE 가 생성 내내 RAM 에서 놀다가 마지막에 올라가는데, 메모리가 빠듯한 PC 에서는 그사이 윈도가 그 RAM 을
    페이지 파일로 내보내 풀기 직전에 몇 분씩 멈췄다(실측). 실행기는 쉬는 시간이 길면 사이드카를 끄므로 그래픽카드를 오래 잡지 않는다."""
    from diffusers import WanImageToVideoPipeline, WanPipeline

    parts = _parts(ctx, progress, variant)
    key = (variant, i2v)
    if _CACHE["key"] == key:
        return _CACHE["pipe"]
    if _CACHE["pipe"] is not None:
        _CACHE["pipe"].remove_all_hooks()
    common = dict(tokenizer=None, text_encoder=None, **parts)
    pipe = WanImageToVideoPipeline(**common, image_processor=None, image_encoder=None) if i2v else WanPipeline(**common)
    if ctx.device == "cuda":
        # 고리 순서에서 VAE 를 빼고 '내리지 않는 부품'으로 두면 enable_model_cpu_offload 가 VAE 를 그래픽카드에 둔다.
        pipe.model_cpu_offload_seq = "transformer->transformer_2"
        pipe._exclude_from_cpu_offload = ["vae"]
        pipe.enable_model_cpu_offload()
    pipe.set_progress_bar_config(disable=True)
    _CACHE.update(key=key, pipe=pipe)
    return pipe


def _offload(pipe):
    """멈추거나 실패했을 때 그래픽카드에 남은 모델을 RAM 으로 내린다(끝까지 돌면 파이프라인이 스스로 내린다)."""
    try:
        pipe.maybe_free_model_hooks()
    except Exception:  # noqa: BLE001 — 정리 중 실패는 원래 오류를 가리지 않게 넘긴다
        pass


# ---------------------------------------------------------------------------
# 크기 · 영상 쓰기
# ---------------------------------------------------------------------------


def _size(variant, resolution, orientation, image):
    """(가로, 세로). 이미지가 있으면 비율은 이미지를, 넓이는 고른 해상도를 따른다(32 의 배수 — VAE 16 × 패치 2)."""
    w, h = VARIANTS[variant]["sizes"][resolution]
    mod = 32 if VARIANTS[variant]["family"] == "5b" else 16
    if image is not None:
        area = w * h
        ratio = image.height / image.width
        height = max(mod, round(math.sqrt(area * ratio)) // mod * mod)
        width = max(mod, round(math.sqrt(area / ratio)) // mod * mod)
        return width, height
    return (h, w) if orientation == "portrait" else (w, h)


def _frames(variant, seconds):
    """초 → 프레임 수(4k + 1 — VAE 시간 압축 4)."""
    fps = VARIANTS[variant]["fps"]
    n = int(seconds) * fps
    return n // 4 * 4 + 1


def _write_mp4(path, frames, fps):
    """frames: (n, h, w, 3) float 0~1 → H.264 yuv420p mp4(소리 없음). 크기는 32 의 배수라 짝수다."""
    import av
    import numpy as np

    n, h, w, _ = frames.shape
    container = av.open(path, "w", options={"movflags": "+faststart"})
    try:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height = w, h
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "16", "preset": "medium"}
        for i in range(n):
            arr = (np.clip(frames[i], 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
            frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(arr), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    finally:
        container.close()


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------


def wan22(ctx, inputs, settings):
    import torch
    from PIL import Image, ImageOps

    t0 = time.monotonic()
    prompt = (inputs.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("프롬프트를 이어 주세요. 무엇이 어떻게 움직이는지 한두 문장으로 적으면 됩니다.")
    variant = settings["model"]
    family = VARIANTS[variant]["family"]
    image_path = inputs.get("image")
    if family == "t2v" and image_path:
        raise ValueError("'14B 글→영상'은 시작 프레임을 받지 않습니다. 시작 프레임을 쓰려면 '14B 이미지→영상'이나 5B 를 고르세요.")
    if family == "i2v" and not image_path:
        raise ValueError("'14B 이미지→영상'은 시작 프레임이 있어야 합니다. 그림을 시작 프레임에 이어 주세요.")
    if ctx.device != "cuda":
        raise ValueError("Wan 2.2 는 NVIDIA 그래픽카드(CUDA)에서만 돕니다. 이 PC 에서는 그래픽카드를 찾지 못했습니다.")

    progress = Progress(ctx, 0.0, 1.0)
    image = None
    if image_path:
        with Image.open(image_path) as im:
            im.load()
            image = ImageOps.exif_transpose(im).convert("RGB")
    width, height = _size(variant, settings["resolution"], settings["orientation"], image)
    num_frames = _frames(variant, settings["seconds"])
    steps = VARIANTS[variant]["steps"] if settings["steps"] == "default" else int(settings["steps"])
    guidance, guidance_2 = VARIANTS[variant]["guidance"]
    seed = int(settings["seed"])
    if seed < 0:
        seed = random.randint(0, 2**31 - 1)
    negative = (settings.get("negative") or "").strip() or DEFAULT_NEGATIVE
    ctx.log(f"{variant} {width}x{height} {num_frames}f {steps} steps seed {seed} image={bool(image)}")

    pipe = None
    try:
        load = Progress(ctx, 0.0, 0.08)
        embeds = _encode(ctx, load, [prompt, negative])
        pipe = _pipeline(ctx, load, variant, i2v=image is not None)
        if image is not None:
            image = image.resize((width, height), Image.LANCZOS)
        ctx.check_cancel()

        denoise = Progress(ctx, 0.08, 0.92)
        loop_start = [None]

        def on_step(p, i, t, kwargs):
            ctx.check_cancel()
            now = time.monotonic()
            if loop_start[0] is None:
                loop_start[0] = now
            done = i + 1
            denoise(done / steps, "denoise")
            if done >= 2:
                per = (now - loop_start[0]) / max(1, done - 1)
                total = (now - t0) + per * (steps - done) * (1 + DECODE_SHARE)
                if total > TIME_BUDGET_SEC:
                    raise TimeoutError(
                        f"이 PC 에서는 이 설정으로 약 {math.ceil(total / 60)}분이 걸려 한 번에 돌 수 있는 시간(약 {TIME_BUDGET_SEC // 60}분)을"
                        " 넘습니다. 길이를 줄이거나 해상도를 480P 로, 단계를 줄여 다시 실행해 주세요."
                    )
            return kwargs

        call = dict(
            prompt_embeds=embeds[0],
            negative_prompt_embeds=embeds[1],
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=torch.Generator(device="cpu").manual_seed(seed),
            output_type="np",
            callback_on_step_end=on_step,
        )
        if guidance_2 is not None:
            call["guidance_scale_2"] = guidance_2
        if image is not None:
            call["image"] = image
        torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            frames = pipe(**call).frames[0]
        peak = torch.cuda.max_memory_allocated() / 1024**3
        ctx.check_cancel()
        progress(0.97, "encode")
        out = ctx.out_path("wan22.mp4")
        _write_mp4(out, frames, VARIANTS[variant]["fps"])
        ctx.log(f"done in {time.monotonic() - t0:.1f}s, peak torch VRAM {peak:.2f} GiB")
    except torch.OutOfMemoryError:
        _drop_pipe()
        raise RuntimeError(_oom_message(variant)) from None
    except BaseException:
        if pipe is not None:
            _offload(pipe)
        raise
    finally:
        _free_cuda()
    ctx.progress(1.0, "done")
    return {"video": out}


NODES = {"wan22": wan22}
