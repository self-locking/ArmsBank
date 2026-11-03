
import subprocess
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent

# 原始 WAV 音乐目录
INPUT_DIR = BASE_DIR / "data" / "music"

# 转换后的 MP3 输出目录
OUTPUT_DIR = BASE_DIR / "data" / "music_mp3"

# MP3 音质：192 kbps
BITRATE = "192k"


def convert_wav_to_mp3():
    if not INPUT_DIR.exists():
        print(f"找不到音乐目录：{INPUT_DIR}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    wav_files = list(INPUT_DIR.glob("*.wav"))

    if not wav_files:
        print("没有找到 WAV 文件。")
        return

    print(f"找到 {len(wav_files)} 个 WAV 文件，开始转换...\n")

    success = 0
    failed = 0

    for index, wav_file in enumerate(wav_files, start=1):
        # 保留原始文件名，只修改扩展名
        mp3_file = OUTPUT_DIR / (wav_file.stem + ".mp3")

        # 已存在的文件不重复转换
        if mp3_file.exists():
            print(f"[跳过] {wav_file.name}")
            continue

        command = [
            "ffmpeg",
            "-y",
            "-i", str(wav_file),
            "-vn",
            "-codec:a", "libmp3lame",
            "-b:a", BITRATE,
            "-map_metadata", "0",
            str(mp3_file)
        ]

        print(f"[{index}/{len(wav_files)}] 正在转换：{wav_file.name}")

        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            original_size = wav_file.stat().st_size / (1024 * 1024)
            compressed_size = mp3_file.stat().st_size / (1024 * 1024)

            print(
                f"  完成：{original_size:.2f} MB"
                f" -> {compressed_size:.2f} MB"
            )

            success += 1

        except subprocess.CalledProcessError:
            print(f"  转换失败：{wav_file.name}")
            failed += 1

    print("\n========== 转换完成 ==========")
    print(f"成功：{success}")
    print(f"失败：{failed}")
    print(f"输出目录：{OUTPUT_DIR}")


if __name__ == "__main__":
    convert_wav_to_mp3()
