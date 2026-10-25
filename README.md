# Wan 2.2 — 글 · 이미지로 영상 만들기

PromBoard 커뮤니티 플러그인입니다. 캔버스에 **Wan 2.2 영상** 노드 하나를 더합니다. 알리바바 Wan 팀이 공개한 Wan 2.2 로 프롬프트(글)에서, 또는 시작 프레임 그림에서 이어지는 영상을 이 PC 에서 만듭니다(소리 없음).

| 글→영상 (720P · 3초 · 20단계) | 이미지→영상 (시작 프레임 → 720P · 2초 · 20단계) |
|---|---|
| ![글→영상: 해 뜨는 눈 덮인 소나무 숲을 걷는 붉은 여우](docs/t2v-720p-3s.gif) | ![시작 프레임](docs/i2v-start.jpg) ![이미지→영상: 여우가 눈보라를 일으키며 뛴다](docs/i2v-720p-2s.gif) |

프롬프트(글→영상): `A red fox trots through fresh snow in a quiet pine forest at sunrise, soft golden light, the camera slowly tracks alongside.` — 5B GGUF Q8, 시드 42. GIF 는 480px · 12fps 로 줄인 것이고 실제 결과는 1280×704 · 24fps mp4 입니다.

## 입력과 결과

| 포트 | 종류 | 뜻 |
|---|---|---|
| 프롬프트 | 글(필수) | 무엇이 어떻게 움직이는지 한두 문장 |
| 시작 프레임 | 그림(선택) | 이으면 그 그림에서 시작하는 영상(이미지→영상). 화면 비는 그림을 따릅니다 |
| 영상 | 영상 | mp4(H.264, yuv420p), 소리 없음. 5B 24fps · 14B 16fps |

## 판 (설정 '모델', 고른 판의 파일만 받습니다)

| 판 | 하는 일 | 받는 것 | 크기 |
|---|---|---|---|
| 5B GGUF Q8 | 글→영상 · 이미지→영상 | 변환기 [QuantStack/Wan2.2-TI2V-5B-GGUF](https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF) Q8_0 + 글 인코더 UMT5-XXL · VAE [Wan-AI/Wan2.2-TI2V-5B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers) | 약 19.6GB |
| 5B 원본 (bf16) | 글→영상 · 이미지→영상 | 공식 [Wan-AI/Wan2.2-TI2V-5B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers) 전부(변환기 fp32 20GB 를 bf16 으로 읽고, 그래픽카드가 20GB 보다 작으면 fp8 로 들고 있다가 계산할 때만 푼다) | 약 34.2GB |
| 14B 글→영상 (검증 안 됨) | 글→영상 | [QuantStack/Wan2.2-T2V-A14B-GGUF](https://huggingface.co/QuantStack/Wan2.2-T2V-A14B-GGUF) 전문가 둘 Q8_0 + 설정 · VAE [Wan-AI/Wan2.2-T2V-A14B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B-Diffusers) + 같은 글 인코더 | 약 42.7GB |
| 14B 이미지→영상 (검증 안 됨) | 이미지→영상 | [QuantStack/Wan2.2-I2V-A14B-GGUF](https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF) 전문가 둘 Q8_0 + [Wan-AI/Wan2.2-I2V-A14B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B-Diffusers) + 같은 글 인코더 | 약 42.7GB |

- 처음 값 **자동**은 이 PC 그래픽카드 메모리가 24GB 이상이면 5B 원본, 그 밖(그래픽카드를 모르는 PC 포함)은 5B GGUF Q8 을 고르고 그 판만 받습니다. 기준은 5B 원본 24GB(공식 RTX 4090 안내) · GGUF Q8 12GB(아래 실측)입니다.
- 글 인코더 · 토크나이저는 네 판이 같은 파일(5B 저장소 것)을 같이 씁니다.
- 14B 두 판은 아직 실제로 돌려 보지 않았습니다(공식 안내는 그래픽카드 80GB 이상, 이 플러그인은 GGUF Q8 과 내리기로 줄여 씁니다). 필요 그래픽카드 메모리는 24GB 로 적었습니다(어림값 — 쓸 차례의 Q8 전문가 하나 15.4GB 를 통째로 올리고, 아래 5B 실측의 계산 몫보다 큰 720P 계산 몫을 더한 값).

## 설정

- 해상도: 720P(5B 1280×704 · 14B 1280×720) · 480P(832×480). 5B 는 720P 로 배운 모델이라 480P 는 빠르지만 화질이 떨어집니다.
- 화면 방향: 시작 프레임이 없을 때 가로 16:9 · 세로 9:16.
- 길이 · 단계: 공식 값은 5초 · 50단계(14B 40단계)입니다. 이 플러그인의 기본은 12GB 그래픽카드에서 한 번 실행 시간 안에 끝나는 **3초 · 30단계**입니다. 12GB 에서 5B 는 720P 3초까지 돌고, 5초는 480P 로 돌립니다(아래 실측).
- 시드, 피할 것(비우면 Wan 공식 기본 부정 프롬프트).
- 공식 값(Wan2.2 README · diffusers 예시): 5B 24fps, guidance 5.0, UniPC 스케줄러. 14B 16fps, guidance 글→영상 4.0/3.0 · 이미지→영상 3.5/3.5.

## 실측 (RTX 4070 SUPER 12GB · RAM 64GB · Windows, 5B GGUF Q8)

| 설정 | 걸린 시간 | 그래픽카드 메모리 최대 |
|---|---|---|
| 720P · 2초 · 20단계, 글→영상 | 3분 18초 | 8.7GiB (torch) |
| 720P · 2초 · 20단계, 이미지→영상 | 3분 44초 | 8.7GiB |
| **720P · 3초 · 30단계 (기본 설정)** | 7분 12초 | 9.6GiB |
| 720P · 3초 · 20단계 | 5분 3초 | 9.7GiB |
| 720P · 3초 · 50단계 (공식 단계) | 14분 54초 | 9.7GiB |
| 480P · 5초 · 20단계 | 3분 31초 | 8.8GiB |
| 720P · 5초 · 20단계 | 그래픽카드 메모리를 넘어(torch 11.7GiB) 느려져, 노드가 6번째 단계에서 어림 18분으로 멈춤 | — |

- 시간은 모델을 이미 읽어 둔 상태에서 잰 노드 한 번(프롬프트 임베딩 · 생성 · 풀기 · mp4 쓰기)입니다. 기본 설정 줄은 1.0.1 코드(생성 중 VAE 를 RAM 으로)로 다시 잰 값이고, 나머지 줄은 1.0.0 코드로 잰 값입니다. 처음 실행은 모델 읽기(글 인코더 11.4GB · 변환기 5.4GB)가 더해집니다.
- 그래픽카드 메모리는 `torch.cuda.max_memory_allocated` 입니다. nvidia-smi 로 본 전체 사용량은 11.9GB 까지 올라갔습니다(같은 PC 의 다른 프로그램 몫 약 1.3GB 포함).
- 단계 하나는 720P 2초 약 7초, 720P 3초 약 12~16초입니다.
- 5B 원본 판과 14B 두 판은 이 PC 에서 돌려 보지 않았습니다.

## 라이선스와 조건

- 플러그인 코드: Apache-2.0([LICENSE](LICENSE)).
- 모델 가중치: Wan 2.2 는 Apache-2.0 입니다(GGUF 변환본도 같은 라이선스). 매출 상한 · 지역 제한 · 사용 승인 절차가 없습니다. 만든 영상의 권리와 책임은 사용자에게 있습니다(모델 카드).

## 설치

1. PromBoard 앱 → 플러그인 매니저 → **커뮤니티 플러그인** → 둘러보기에서 설치합니다.
2. 코드가 든 플러그인이라 **제한 모드**를 끄고 플러그인을 켭니다.
3. 처음 실행할 때 실행 준비가 파이썬 패키지(diffusers · transformers · gguf 등)와 고른 판의 모델을 받습니다.

## 플러그인 만드는 사람을 위한 메모

```
manifest.json            플러그인 정보 · 노드 정의 · 모델(requires.models, 판마다 when)
nodes/main.py            NODES = {"wan22": 함수} — 글 인코더로 임베딩을 만든 뒤 WanPipeline / WanImageToVideoPipeline 으로 생성
nodes/requirements*.txt  받을 패키지 · 운영체제별 해시 잠금 파일(Windows · Linux)
```

- 12GB 에서 돌리는 길: 글 인코더(bf16 11.4GB)는 층(leaf) 단위로 올렸다 내리며 임베딩을 만들고 파이프라인에는 넣지 않습니다. 변환기는 파이프라인 단위 내리기(enable_model_cpu_offload)로 쓸 때만 올리고, VAE(16GB 보다 작으면 bf16)는 시작 그림 줄이기 · 풀기 때만 그래픽카드에 두고 생성 중에는 RAM 으로 내립니다. 마지막 단계 뒤에는 변환기를 내리고 풉니다. 다른 프로그램이 그래픽카드를 쓰고 있으면 12GB 를 넘겨 윈도가 공유 메모리로 넘기는데, 그러면 몇 배 느려집니다.
- 앞 단계 속도로 전체 시간을 어림해 PromBoard 의 한 번 실행 시간(약 15분)을 넘을 것 같으면 바로 멈추고 줄일 설정을 알립니다.
- 잠금 파일 다시 만들기: `uv pip compile nodes/requirements.in --generate-hashes --python-version 3.12 --python-platform x86_64-pc-windows-msvc --index-url https://pypi.org/simple --exclude-newer 2026-10-25T00:00:00Z` (리눅스는 `x86_64-manylinux_2_28`). torch 줄은 앱이 빼고 앱의 torch 2.8.0 을 먼저 깝니다(`"torch": "host"`).

---

## English

A PromBoard community plugin. It adds one **Wan 2.2 video** node that runs Alibaba's open Wan 2.2 on your PC: text-to-video from a prompt, or image-to-video from a start frame (no audio).

- Inputs: prompt (required), start frame (optional; the aspect ratio follows the image). Output: an H.264 MP4 without audio (5B 24 fps, 14B 16 fps).
- Variants (only the chosen one is downloaded; the default **Auto** picks 5B original on 24 GB+ GPUs and 5B GGUF Q8 otherwise): 5B GGUF Q8 (QuantStack Wan2.2-TI2V-5B GGUF Q8_0 transformer + the official UMT5-XXL text encoder and VAE, about 19.6 GB), 5B original (the official Diffusers repo, about 34.2 GB; the fp32 transformer is loaded as bf16 and kept in fp8 below 20 GB of VRAM), 14B text-to-video and 14B image-to-video (two GGUF Q8_0 experts each, about 42.7 GB; officially 80 GB+ VRAM, reduced here with GGUF Q8 and offloading to an estimated 24 GB — the active 15.4 GB Q8 expert is moved on whole, plus a 720P compute share larger than the 5B measurement below; **unverified**).
- Settings: resolution (720P / 480P — the 5B model was trained at 720P), orientation, length, steps (official: 5 s, 50 steps; 40 for 14B — the plugin defaults to **3 s · 30 steps**, which fits a 12 GB card within one run), seed, negative prompt (empty = the official Wan default).
- 12 GB path: the text encoder is offloaded layer by layer (leaf level) and used only to make embeddings; the transformer uses model CPU offload; the VAE (bf16 below 16 GB) is on the GPU only for encoding the start frame and decoding, and in RAM during sampling; the transformer is offloaded before decoding. If other programs use the GPU and the total goes over 12 GB, Windows spills into shared memory and generation gets several times slower. The node estimates the total time from the first steps and stops early when it would exceed PromBoard's single-run limit (about 15 minutes).

- Measured on an RTX 4070 SUPER 12 GB (64 GB RAM, Windows), 5B GGUF Q8, models already loaded: 720P 2 s 20 steps 3 min 18 s (text) / 3 min 44 s (image), **720P 3 s 30 steps (the default) 7 min 12 s** (re-measured with 1.0.1, the other rows with 1.0.0), 720P 3 s 20 steps 5 min 3 s, 720P 3 s 50 steps (official) 14 min 54 s, 480P 5 s 20 steps 3 min 31 s; peak `torch.cuda.max_memory_allocated` 8.7–9.7 GiB. 720P 5 s exceeds 12 GB (11.7 GiB) and slows down, so the node stops itself at step 6 with an 18-minute estimate. The 5B original and both 14B variants were not run here.

- License: plugin code Apache-2.0; Wan 2.2 weights (and the GGUF conversions) Apache-2.0, no revenue cap, regional restriction or access gate.
- **Install**: PromBoard → Plugin Manager → **Community plugins** → Browse → install. Turn off **Restricted mode** and enable the plugin. The first run prepares the Python packages and the chosen variant's models.

The plugin texts in the app are Korean only for now — the plugin manifest takes a single language.

## License

Plugin code: Apache-2.0 — see [LICENSE](LICENSE). The Wan 2.2 weights are Apache-2.0 and are downloaded from Hugging Face, not bundled.
