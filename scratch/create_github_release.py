#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path
import urllib.request
import urllib.error

REPO = "imymi/ComfyUI-IYKYK"
TAG = "v1.1.0-rc10"
DIST_DIR = Path("dist/v1.1.0-rc10-release-adbc7e1b4146")

RELEASE_BODY = """## v1.1.0-rc10

- **全量词库合流与结构化变体**：
  - 服装词库扩充至 165 款大类（新增 Batch 6 民族传统、工装战术、牛仔下装与内衣鞋靴等 28 款），全量场景扩充至 234 个，显式 UI 选项增至 837 项。
  - 引入款式受控抽样两步契约（独立主款/版型与相容属性），杜绝同款式多长句暴力拼接。
  - 存量 35 款具备变体角色或互斥组的款式执行受控独立采样；无元数据存量 23 款保持 NONE 模式 0 次 RNG 消费向后兼容。
- **边界安全与 L1/L5 纯净度防护**：
  - 复合穿搭池（`clothing_ensemble_combos`）增加 L1 敏感词过滤与严格单选，合规候选为空时 Fail-Closed 返回空列表，严禁放行未过滤池；
  - 直达标签（`target_tag`）同步落实 L1 敏感词拦截；
  - 敏感拍摄视角（窥底 `upskirt perspective`、俯视深沟等）在 L1 模式下置空拦截。
- **冲突消解与载体相容性加固**：
  - 修复解扣动作与载体实体（outerwear / top / one_piece）拓扑相容性绑定规则，隔离不兼容解扣动作并标记 `state_lacks_carrier` 或 `one_piece_state_conflict`；
  - 强化显式目标绑定（`target_id`）隔离性，目标不兼容时返回 `UNBOUND_INCOMPATIBLE`，绝不回退改绑其他载体。
- **质量门禁与审计闭环**：
  - 服装兼容性测试落实双基线守恒：恢复 `596f43e` 历史黄金哈希（`2d00175f...`）并通过受控历史快照重放验证，独立建立当前 `rc10` 摘要（`fdfe272c...`）；
  - 消除快照缺失静默跳过：支持从 Git 归档自动就绪受控历史快照，并新增缺失目录/无效提交显式报错反例门禁；
  - 万种子门禁实行历史缓存内容完整性验证：逐条计算 10,000 种子输出正向提示词 SHA-256 并实时聚合断言，加单字节篡改阻断反例；
  - 全矩阵 CI（包含 Python 3.9、3.10 兼容性检查及 Python 3.11、3.12 全量 576 项单元回归与双构建校验）已在 GitHub Actions（运行 [38038400337](https://github.com/imymi/ComfyUI-IYKYK/actions/runs/38038400337)）全量 100% 通过（`conclusion: success`）。

同一 `prompt_seed` 在 rc9 与 rc10 之间可能产生不同提示词；升级后请重新核对依赖固定 seed 的工作流。同一 rc10 版本及相同输入保持确定性。

- **Release ZIP SHA-256**: `adbc7e1b414627933a53ef7305e94a7502148d102c52882c60e4df781b57cac1`
- **文件清单数量**: 43 个核心运行时文件
- **权威发布提交**: `843d9cebe1ecf4cbd630f47fd5d4b0767a862bdc`
"""

def get_token():
    cmd = 'printf "protocol=https\\nhost=github.com\\n\\n" | git credential fill'
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise RuntimeError("GitHub token not found in git credentials")

def main():
    token = get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Release-Publisher/1.0",
    }

    # 1. 检查 release 是否已存在
    get_url = f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
    req = urllib.request.Request(get_url, headers=headers)
    release_data = None
    try:
        with urllib.request.urlopen(req) as resp:
            release_data = json.load(resp)
            print(f"Existing release found: {release_data['id']}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("Release does not exist yet. Creating new release...")
        else:
            raise

    # 2. 如果不存在，则创建
    if release_data is None:
        post_url = f"https://api.github.com/repos/{REPO}/releases"
        payload = {
            "tag_name": TAG,
            "target_commitish": "843d9cebe1ecf4cbd630f47fd5d4b0767a862bdc",
            "name": TAG,
            "body": RELEASE_BODY,
            "draft": False,
            "prerelease": True,
        }
        req = urllib.request.Request(
            post_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            release_data = json.load(resp)
            print(f"✅ Created release: {release_data['html_url']} (ID: {release_data['id']})")

    upload_url_template = release_data["upload_url"] # e.g. "https://uploads.github.com/repos/.../assets{?name,label}"
    upload_base_url = upload_url_template.split("{")[0]

    # 3. 上传三个资产
    existing_assets = {a["name"]: a["id"] for a in release_data.get("assets", [])}

    assets_to_upload = [
        ("ComfyUI-IYKYK-v1.1.0-rc10.zip", "application/zip"),
        ("MANIFEST.json", "application/json"),
        ("SHA256SUMS.txt", "text/plain"),
    ]

    for filename, content_type in assets_to_upload:
        file_path = DIST_DIR / filename
        if not file_path.exists():
            raise FileNotFoundError(f"Missing asset file: {file_path}")

        file_bytes = file_path.read_bytes()

        # 如果已有同名资产，先删除
        if filename in existing_assets:
            print(f"Deleting existing asset {filename} (ID: {existing_assets[filename]})...")
            del_url = f"https://api.github.com/repos/{REPO}/releases/assets/{existing_assets[filename]}"
            del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
            with urllib.request.urlopen(del_req) as resp:
                pass

        print(f"Uploading {filename} ({len(file_bytes)} bytes)...")
        upload_url = f"{upload_base_url}?name={filename}"
        up_req = urllib.request.Request(
            upload_url,
            data=file_bytes,
            headers={**headers, "Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(up_req) as resp:
            asset_res = json.load(resp)
            print(f"✅ Uploaded {filename} -> {asset_res['browser_download_url']}")

    print("\n🎉 GitHub Release published successfully!")
    print(f"Release URL: {release_data['html_url']}")

if __name__ == "__main__":
    main()
