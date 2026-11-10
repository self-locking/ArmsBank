import os
import shutil

root = r"C:\Users\example_one\Desktop\database\armsbank_app\data\history\txt"
old = "驱逐坦克"
new = "坦克歼击车"
backup = os.path.join(root, "_backup")

for name in os.listdir(root):
    if not name.lower().endswith(".txt"):
        continue
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        continue

    # 先按 UTF-8 读，失败再按 GBK 读
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        enc = "utf-8"
    except UnicodeDecodeError:
        with open(path, "r", encoding="gbk") as f:
            content = f.read()
        enc = "gbk"

    if old in content:
        os.makedirs(backup, exist_ok=True)
        shutil.copy2(path, os.path.join(backup, name))
        content = content.replace(old, new)
        with open(path, "w", encoding=enc, newline="") as f:
            f.write(content)
        print("已修改:", name, "编码:", enc)

print("完成，备份在:", backup)