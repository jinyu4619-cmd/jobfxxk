# jobfxxk — 秋招投递全流程

把一份简历变成一串投递：建信息库 → 按 JD 微调简历（**版式不变**）→ 从岗位库筛岗 → 打开招聘官网填表 → 记进台账。

**唯一的成功标准是「投递成功」**——系统里能查到这条申请。生成了简历、填完了表，都不算。

这是 Codex 的入口文件。完整流程在 `SKILL.md`，字段定义在 `references/`。本文件只写「怎么开始」和「不能违反的几条」。

---

## 开始

用户说「开始」「帮我投简历」「继续」时：

```bash
python scripts/setup.py --check
```

返回一段 JSON。**照它说的做，不要自己判断环境**：

1. `ready: true` → 读 `state.json` 和台账，给三行摘要，进阶段 1
2. `ready: false` → 把 `ask` 原样念给用户，一句话
3. 用户回答后跑 `--apply <对应动作>`，它会再返回一次状态，回到第 2 步

一次只问一件事。`missing` 里是什么就是什么，别因为「看起来装过了」就跳过。

---

## 铁律

1. **提交类按钮永远由用户点**：保存、投递、提交、确定、下一步。你只填文字、读页面、做导航。
2. **下拉、日期、单选**这类控件也留给用户。
3. **不改任何数字**，不编造经历。简历上的省二等奖就是省二等奖，95% 就是 95%。
4. **没确认过的经历不许进简历**（`scripts/profile.py exp confirm` 是硬闸，有数字必须带 `--metrics`）。
5. **敏感信息**（证件号、紧急联系人）只进 DPAPI 加密文件，不进 `profile.json`、不进日志、不进 Git。
6. **不要在用户屏幕上弹窗**，不要用后台分离进程跑长任务。

---

## 阶段

完整描述见 `SKILL.md`，这里是索引：

| 阶段 | 干什么 | 脚本 |
|---|---|---|
| 0 | 启动检查、断点恢复 | `setup.py --check` |
| 1 | 把简历读成文本、逐段核对数字 | `read_resume.py`、`profile.py exp *` |
| 2 | 建个人信息库（只问缺的） | `profile.py missing / set / mode / review` |
| 3 | 定模板、标定行宽 | `make_anchors.py`、`compose.py` |
| 4 | 现拉岗位库、筛岗、读 JD | `sync_jobs.cjs`、`translate_jobs.py`、`filter_jobs.py`、`fetch_jd.cjs` |
| 5 | 定志愿 | 用户定 |
| 6 | 按 JD 微调简历、复验版式 | `fill_engine.py`、`verify.py layout` |
| 7 | 开官网、用户登录 | `browser.cjs launch` |
| 8 | 上传简历、填文字框 | `browser.cjs upload / fill` |
| 9 | 核对，用户提交 | — |
| 10 | 写台账 | `write_tracker.py`、`verify.py tracker` |

每进入新阶段，先记进度：

```bash
python scripts/state.py set --phase "阶段 6 · 微调简历" --company 某公司 --position 某岗位
```

---

## 让用户少打字

需要用户做选择时，**给编号选项，让他回一个数字**，不要让他复述选项内容：

```
简历怎么来？
1. 用内置模板
2. 我有 Word 简历
3. 只有 PDF
4. 其他（直接说你想怎么办）
回复数字，或者直接说。
```

**最后一格永远留「其他」**，用户说什么就接住什么，别硬往选项里归类。收到「2」就往下走；收到一句话，就按那句话办。

Claude 桌面版那边这条规矩是用选项按钮实现的（见 `SKILL.md` 的「一律用按钮」一节），按钮的最后一格同样是可自定义的输入框。Codex 没有按钮，用编号加「直接说」代替，效果一样。

---

## 环境前提

- **Windows + Microsoft Word**。导出 PDF 走 Word 的 COM 接口，Mac/Linux 跑不通这一步。
- Python 3.9+、Node 20+。
- 依赖（Playwright、pypdf）由 `setup.py --apply install_deps` 装到数据目录，不装进项目目录。
- **需要联网**：岗位库是飞书公开表、JD 要抓页面。Codex 的沙箱默认可能禁网，跑之前确认允许网络访问。

---

## 文件在哪

```
jobfxxk/            代码，可发布，不含个人信息
jobfxxk-data/       个人数据：配置、锚点表、岗位缓存、加密信息
简历库/
├── docx/           母版 + 每家一个版本
└── pdf/            同名 PDF
```

数据目录默认在用户主目录下 `jobfxxk-data`，也可以用环境变量 `JOBFXXK_DATA` 指到别处。

---

## 常见坑

- **中文别走管道**：`echo '{...}' | python` 在 Windows 下会把中文变成 `?????`。一律写成 UTF-8 文件再传 `--file`。
- **改过 docx 就要重导 PDF**，否则锚点表是旧的。`verify.py resume` 会拦。
- **点开职位会新开标签页**，用 `browser.cjs open <url>` 在同一个标签里导航，`close-tabs` 清理。
- **上传简历会重置已填字段**，所以先传简历、再填字，填完先点「暂存」。
