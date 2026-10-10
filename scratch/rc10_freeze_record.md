# ComfyUI-IYKYK v1.1.0-rc10 候选冻结落盘记录

- **锁定版本**：`v1.1.0-rc10`
- **PR 验证 HEAD**：`6b301756c439dcb06f6a3a62bf0e9a8c4cf317f2`
- **Merge Commit (main)**：`4d267c503e3bc3bb76e188847304e89e69749487`
- **冻结时间戳**：2026-10-09T01:31:00Z
- **文件计数**：43
- **候选发布 ZIP SHA-256（Candidate Mode）**：
  ```text
  64407483bfa704f44b3ff9a270e6ccb7a121ac7619e2758a8d8529ea030593bd
  ```
- **双构建一致性状态**：
  - Build A SHA-256: `64407483bfa704f44b3ff9a270e6ccb7a121ac7619e2758a8d8529ea030593bd`
  - Build B SHA-256: `64407483bfa704f44b3ff9a270e6ccb7a121ac7619e2758a8d8529ea030593bd`
  - 产物差异：Bit-for-Bit Identical (零差异)

---

## 测试分阶段归档摘要

1. **阶段 A：全量回归基线（原始 L1 反例修复后、收尾加固前）**
   - 范围：574 项全量测试
   - 结果：Ran 574 tests in 3553.657s, OK (零 failure, 零 error)

2. **阶段 B：收尾门禁加固后专项复测**
   - 服装 14 项专项：PASS (19.9s)
   - L1 纯净度 6 项专项：PASS (19.3s)
   - RC8 5 项质量门禁：PASS (498.8s)

详细 29 项原始失败台账参见：`docs/v1.1.0-rc10_freeze_report.md`。
