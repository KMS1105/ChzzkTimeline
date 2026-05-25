# Timeline.py
import os
import sys
import json
import warnings
import subprocess
from yt_dlp import YoutubeDL
from google import genai
from google.genai import types

warnings.filterwarnings("ignore", category=UserWarning)

CONFIG_FILE = "config.json"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "TARGET_CHANNEL_ID": "스트리머의_32자리_채널_ID_입력",
            "GEMINI_API_KEY": "AIzaSy..."
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        print(f"\n⚙️  [안내] 프로젝트 폴더에 '{CONFIG_FILE}' 파일이 생성되었습니다.")
        sys.exit(0)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("TARGET_CHANNEL_ID", "").strip(), config.get("GEMINI_API_KEY", "").strip()
    except Exception as e:
        print(f"❌ [JSON 파싱 실패] config.json 파일을 읽는 중 오류 발생: {e}")
        sys.exit(1)


def get_gemini_client(api_key):
    return genai.Client(api_key=api_key)


def get_video_duration(chzzk_url):
    ydl_opts = {'quiet': True, 'nocheckcertificate': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(chzzk_url, download=False)
        return info.get('duration', 0)


def download_chzzk_vod_audio(chzzk_url, vod_id, start_percent=0.0, end_percent=100.0, output_filename="chzzk_vod_audio"):
    """
    [개선된 오디오 수집 엔진]
    1. 보존형: voicepalette/VOD_{vod_id} 폴더에 데이터를 남겨 다음 실행 시 즉시 재사용
    2. 무손실: 원본 조각 파일을 삭제하지 않음
    """
    # 1. 고유 폴더 경로 생성 (voicepalette/VOD_ID)
    specific_palette_dir = os.path.join(os.getcwd(), "voicepalette", f"VOD_{vod_id}")
    os.makedirs(specific_palette_dir, exist_ok=True)
    
    # 최종 결과물 경로
    final_output_mp3 = os.path.join(specific_palette_dir, f"{output_filename}.mp3")
    raw_audio_tmpl = os.path.join(specific_palette_dir, "raw_full_audio")
    
    # 2. 캐시 확인: 이미 컷팅된 파일이 존재하면 즉시 반환 (네트워크 통신 차단)
    if os.path.exists(final_output_mp3):
        print(f"\n✨ [캐시 적중] 이미 저장된 오디오를 사용합니다: {final_output_mp3}")
        return final_output_mp3

    print(f"\n🎵 1단계: 치지직 오디오 데이터 수집 엔진 가동 (멀티스레드 가속)...")
    
    total_duration = get_video_duration(chzzk_url)
    if total_duration == 0:
        print("❌ VOD 메타데이터 파싱 실패.")
        return ""
        
    start_secs = int(total_duration * (start_percent / 100.0))
    end_secs = int(total_duration * (end_percent / 100.0))
    duration_secs = end_secs - start_secs
    
    # 3. yt-dlp 옵션: 조각 파일 보존을 위한 keepvideo 설정
    ydl_opts = {
        'format': 'worstaudio/worst',
        'outtmpl': f'{raw_audio_tmpl}.%(ext)s',
        'keepvideo': True,             # 👈 [핵심] 다운로드 후 원본 조각 파일 유지
        'quiet': True,
        'nocheckcertificate': True,
        'concurrent_fragment_downloads': 16,  
        'socket_timeout': 3,
        'retries': 3,
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'noplaylist': True,
    }

    print("📥 [네트워크 가속] 16스레드 병렬 통신으로 보이스 파레트 낚아채는 중...")
    with YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(chzzk_url, download=True)
        downloaded_ext = info_dict.get('ext', 'ts')
        downloaded_raw_path = f"{raw_audio_tmpl}.{downloaded_ext}"

    if not os.path.exists(downloaded_raw_path):
        print("❌ 원본 오디오 스트림 다운로드 실패.")
        return ""

    print("⚡ [로컬 가속] 타겟 구간 오디오 칼정밀 단순 컷팅 진행 중...")
    
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg_bin = "ffmpeg"

    cmd = [
        ffmpeg_bin, '-y',
        '-ss', str(start_secs),
        '-i', downloaded_raw_path,
        '-t', str(duration_secs),
        '-acodec', 'libmp3lame',
        '-b:a', '96k',
        final_output_mp3
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 💡 [핵심 교정]: downloaded_raw_path 삭제 코드 제거 (원본 보존)
    print("✅ 타겟 구간 지정 오디오 고속 워프 추출 및 원본 파일 보존 완료!")
    return final_output_mp3


def transcribe_chzzk_audio(audio_path, chzzk_url, start_percent, model_size="tiny"):
    """
    2단계: [절대 시간 오프셋 복원 버전]
    잘려진 오디오 조각의 상대 시간을 원본 VOD의 절대 시간대(HH:MM:SS)로 자동 역산하여 스크립트를 생성합니다.
    """
    print(f"\n🎙️ 2단계: Faster-Whisper AI 엔진 구동 ({model_size}) - 대본 추출 및 시간 복원 중...")
    
    if not os.path.exists(audio_path):
        print("❌ 분석할 오디오 파일이 존재하지 않습니다.")
        return ""

    # 1. 원본 VOD의 전체 길이 정보를 가져와 9% 지점이 실제 몇 초였는지 절대 시작 시각 역산
    total_duration = get_video_duration(chzzk_url)
    start_secs = int(total_duration * (start_percent / 100.0))
    
    try:
        from faster_whisper import WhisperModel
        # CPU 환경에서 안정적이고 빠르게 돌아가도록 int8 경량화 모델 로드
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except ImportError:
        print("❌ faster-whisper 라이브러리가 설치되어 있지 않습니다.")
        return ""

    # 오디오 분석 가동
    segments, info = model.transcribe(
        audio_path, 
        beam_size=5,
        word_timestamps=False,
        repetition_penalty=1.4,          # 뇌절/도배 방지용 패널티
        compression_ratio_threshold=1.8  # 환각 현상 제어 역치
    )
    
    script_lines = []
    
    for segment in segments:
        # 💡 [핵심 교정]: 조각 파일의 시간(segment.start)에 원본 시작 초(start_secs)를 더함
        absolute_secs = int(segment.start + start_secs)
        
        # 보정된 절대 초 데이터를 기반으로 정확한 시, 분, 초 역산
        h = absolute_secs // 3600
        m = (absolute_secs % 3600) // 60
        s = absolute_secs % 60
        
        # 깨끗하게 포맷팅된 문장 생성
        timestamp_str = f"[{h:02d}:{m:02d}:{s:02d}]"
        text_content = segment.text.strip()
        
        if text_content:
            script_lines.append(f"{timestamp_str} {text_content}")
            # 터미널 실시간 모니터링 출력
            print(f"  {timestamp_str} {text_content}")

    raw_script = "\n".join(script_lines)
    
    # 디버깅 및 무조건 보존을 위해 temp_audio 폴더 안에 텍스트 파일로 영구 저장
    script_cache_path = audio_path.replace(".mp3", "_raw_script.txt")
    with open(script_cache_path, "w", encoding="utf-8") as f:
        f.write(raw_script)
        
    print(f"✅ 원본 오프셋이 복원된 생대본 추출 완료! (보존 경로: {script_cache_path})")
    return raw_script


def generate_chzzk_timeline(raw_script, api_key, actual_title="VOD제목", chzzk_url=""):
    """
    3~4단계: 구글 제미나이 정밀 가중치 필터링 및 타임라인 헤더 최종 후처리 함수
    """
    client = get_gemini_client(api_key)
    
    # 💡 [설명조 종결어미 전면 금지 및 숏폼 클립 타이틀 스타일 강제 프롬프트]
    system_instruction = (
        "당신은 인터넷 방송 다시보기 VOD 전문 편집자입니다.\n"
        "대본을 분석하여 방송 중 일어난 사건과 상황을 직관적인 명사형 키워드로 요약하세요.\n\n"
        
        "[🔥 핵심 출력 규칙]\n"
        "1. 설명조 금지: 모든 문장은 명사형으로 종결하고, 핵심 상황만 압축하십시오.\n"
        "2. 오프닝 포함: 방송 시작 인사는 내용과 관계없이 반드시 첫 줄에 출력하세요.\n"
        "3. 가중치 필터링 (wt = wf + wi):\n"
        "   - 재미 점수(wf): 감정 변화, 리액션, 비명, 티키타카 (0 ~ 50점)\n"
        "   - 중요 점수(wi): 메인 콘텐츠 시작, 스케줄 공지, 룰 세팅 (0 ~ 50점)\n"
        "4. 단계 분류: 총점(wt) 기준 10점당 1단계씩 분류하며, 4단계 이상만 출력하세요.\n"
        "5. 오타 및 잡담 배제: 맥락 없는 단어나 반복되는 잡담은 무시하십시오.\n\n"
        
        "[📝 출력 형식]\n"
        "서론과 결론 없이 아래 형식만 한 줄씩 출력하세요.\n"
        "[시간] (단계) 요약 내용"
    )

    target_models = ["gemini-2.5-flash", "gemini-1.5-flash"]
    gemini_timeline = ""
    
    for model_name in target_models:
        print(f"\n✨ 3단계: 구글 Gemini API ({model_name}) 기반 타임라인 가공 시도...")
        try:
            response = client.models.generate_content(
            model=model_name,
            contents=f"분석 대상 스크립트:\n{raw_script}\n\n위 규칙을 엄격히 지켜 6단계 이상만 출력하세요:",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                max_output_tokens=16384 # 👈 길게 생성되도록 최대치 할당
            )
        )
            if response.text:
                gemini_timeline = response.text.strip()
                break
        except Exception as e:
            print(f"오류 발생: {e}")
            continue
            
    # 2. [수정] 후처리 로직 단순화
    print("🛠️ 4단계: 타임라인 최종 후처리 중...")
    
    # AI가 생성한 줄들 중에서 [00:00:00]이 들어간 줄을 모두 찾아서 지움 (중복 방지)
    timeline_lines = [line.strip() for line in gemini_timeline.split('\n') if line.strip()]
    timeline_lines = [line for line in timeline_lines if not line.startswith("[00:00:00]")]
    
    # 우리가 원하는 제목으로 첫 줄 강제 고정
    title_header = f"[00:00:00] {actual_title if actual_title else 'VOD제목'}"
    timeline_lines.insert(0, title_header)
    
    return "\n".join(timeline_lines)