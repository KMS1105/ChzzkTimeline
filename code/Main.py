import os
import sys
import shutil
import math

from Chzzk_api import select_chzzk_vod
from Timeline import (
    load_config,
    download_chzzk_vod_audio,
    transcribe_chzzk_audio,
    generate_chzzk_timeline
)

def run_pure_test():
    cleanup_files(specific_palette_dir)
    TARGET_CHANNEL_ID, GEMINI_API_KEY = load_config()

    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("AIzaSy..."):
        print("❌ [환경 설정 에러] config.json 파일에 올바른 GEMINI_API_KEY를 입력해 주세요.")
        return

    VOICE_PALETTE_BASE_DIR = os.path.join(os.getcwd(), "voicepalette")
    os.makedirs(VOICE_PALETTE_BASE_DIR, exist_ok=True)

    # API에서 URL, 제목, 전체 길이를 반환받음
    vod_data = select_chzzk_vod(TARGET_CHANNEL_ID)
    if not vod_data:
        print("❌ 유효한 영상 주소가 확보되지 않아 프로그램을 종료합니다.")
        return
        
    chzzk_url, actual_title, total_duration = vod_data

    vod_id = chzzk_url.split("/")[-1].split("?")[0] if "/" in chzzk_url else "unknown"
    folder_name = f"VOD_{vod_id}"
    specific_palette_dir = os.path.join(VOICE_PALETTE_BASE_DIR, folder_name)

    # 사용자 범위 입력
    start_percent, end_percent = 0.0, 100.0
    while True:
        try:
            user_range = input(
                "📊 분석할 VOD 범위를 %~% 형식으로 입력하세요 (예: 10~15 또는 0~5): "
            ).strip()

            if "~" in user_range:
                parts = user_range.split("~")
                start_percent = float(parts[0].strip())
                end_percent = float(parts[1].strip())
            else:
                start_percent = 0.0
                end_percent = float(user_range)

            if 0.0 <= start_percent < end_percent <= 100.0:
                break
            print("❌ 0~100 사이의 올바른 범위를 입력하세요.")
        except ValueError:
            print("❌ 숫자 형식을 확인하세요.")

    os.makedirs(specific_palette_dir, exist_ok=True)
    
    # 15분(900초) 단위 분할 로직
    start_sec = (start_percent / 100) * total_duration
    end_sec = (end_percent / 100) * total_duration
    chunk_duration = 900
    num_chunks = math.ceil((end_sec - start_sec) / chunk_duration)
    
    all_final_lines = []
    print(f"\n🚀 총 {num_chunks}개 구간으로 나누어 분석을 시작합니다.")

    for i in range(num_chunks):
        curr_start_sec = start_sec + (i * chunk_duration)
        curr_end_sec = min(curr_start_sec + chunk_duration, end_sec)
        
        curr_start_p = (curr_start_sec / total_duration) * 100
        curr_end_p = (curr_end_sec / total_duration) * 100
        
        print(f"\n⏱️ [구간 {i+1}/{num_chunks}] 처리 중: {curr_start_p:.2f}% ~ {curr_end_p:.2f}%")

        cached_script_path = os.path.join(
            specific_palette_dir,
            f"cached_raw_script_{int(curr_start_p)}_{int(curr_end_p)}.txt"
        )

        if os.path.exists(cached_script_path):
            print("✨ [보이스 파레트 적중] 이전에 분석 완료된 구간입니다.")
            with open(cached_script_path, "r", encoding="utf-8") as f:
                full_script = f.read()
        else:
            print("🚀 [최초 분석] 다운로드 및 Whisper 분석을 시작합니다...")
            audio_mp3_path = download_chzzk_vod_audio(
                chzzk_url=chzzk_url,
                vod_id=vod_id, 
                start_percent=curr_start_p,
                end_percent=curr_end_p
            )
            
            if not audio_mp3_path or not os.path.exists(audio_mp3_path):
                print("❌ 다운로드 실패.")
                continue

            full_script = transcribe_chzzk_audio(
                audio_mp3_path,
                chzzk_url=chzzk_url,
                start_percent=curr_start_p
            )

            with open(cached_script_path, "w", encoding="utf-8") as f:
                f.write(full_script)
            print("💾 대본 캐시 저장 완료.")

        result_timeline = generate_chzzk_timeline(
            full_script,
            api_key=GEMINI_API_KEY,
            actual_title=actual_title,
            chzzk_url=chzzk_url
        )

        lines = [line.strip() for line in result_timeline.split("\n") if line.strip()]
        
        # 첫 번째 구간이 아니면 오프닝 인사말 제거
        if i > 0:
            lines = [line for line in lines if "방송 시작 인사" not in line and not line.startswith("[00:00:00]")]
            
        all_final_lines.extend(lines)

    # 결과 병합 및 저장
    new_header = f"[00:00:00] {actual_title}"
    if all_final_lines:
        if all_final_lines[0].startswith("[00:00:00]"):
            all_final_lines[0] = new_header
        else:
            all_final_lines.insert(0, new_header)
    else:
        all_final_lines = [new_header]

    final_timeline = "\n".join(all_final_lines)
    output_path = os.path.join(specific_palette_dir, f"output_timeline_{int(start_percent)}_{int(end_percent)}.txt")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_timeline)

    print("\n==================================================")
    print("🎯 [완성] 구글 Gemini가 빌드한 타임라인 결과")
    print("==================================================")
    print(final_timeline)
    print("==================================================")
    print(f"💾 최종 타임라인 결과 파일이 '{output_path}'로 안전하게 출력되었습니다!")

def cleanup_files(base_dir="voicepalette"):
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            # raw_full_audio.ts를 제외한 모든 파일 삭제
            if file != "raw_full_audio.ts":
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                    print(f"🗑️ 삭제 완료: {file_path}")
                except Exception as e:
                    print(f"❌ 삭제 실패 {file_path}: {e}")

if __name__ == "__main__":
    try:
        run_pure_test()
    except KeyboardInterrupt:
        print("\n🛑 프로그램이 중단되었습니다.")
    except Exception as e:
        print(f"\n❌ 에러 발생: {e}")